"""Aplicación client-side: estado, DOM y editor SVG controlados desde Python."""

from __future__ import annotations

import asyncio
import html
import json
import math
import random
from copy import deepcopy
from typing import Any, Callable

from js import Blob, Object, URL, Worker, document, window
from pyodide.ffi import create_proxy, to_js

from geometry import edge_geometry, fit_transform, has_reverse, node_center, value_color
from models import AppState, Edge, GraphTab, Node, SimulationState
from simulation.algoritmos import ALGORITMOS
from simulation.usuario import CLAVE as CLAVE_USUARIO


STATE = AppState()
PROXIES: list[Any] = []
# Dos workers distintos a proposito: el del barrido se termina al cancelar
# y ENGINE tiene que sobrevivir a eso para seguir atendiendo al editor.
WORKER = None
ENGINE = None
# Un unico proxy retenido para el plazo de la ronda: crear uno por ronda haria
# crecer PROXIES sin fin, porque `retain` no libera nunca.
ROUND_TIMER = None
ROUND_TIMEOUT_PROXY = None
#: Cuanto se le da a una ronda ya con el motor caliente. Una ronda real tarda
#: milisegundos; este plazo existe por el codigo pegado que no termina.
ROUND_TIMEOUT_MS = 15000
DRAG: dict[str, Any] = {}
CONNECTION: dict[str, Any] = {}
PAN: dict[str, Any] = {}
LAST_NODE_CLICK: dict[str, Any] = {}
DIALOG_CALLBACK: Callable[[str], bool] | None = None
CHART_POINTS: list[dict[str, float]] = []
CHART_MAX_Y = 1.0


# La ranura del algoritmo propio sale en el selector junto a los de casa, pero
# su código solo existe dentro del Worker (`simulation.usuario`).
RANURA_USUARIO = {"nombre": "Algoritmo propio…"}


def algoritmo_activo() -> dict[str, Any]:
    if STATE.algorithm == CLAVE_USUARIO:
        return RANURA_USUARIO
    return ALGORITMOS[STATE.algorithm]


def by_id(element_id: str):
    return document.getElementById(element_id)


def retain(callback: Callable):
    proxy = create_proxy(callback)
    PROXIES.append(proxy)
    return proxy


def listen(target, event_name: str, callback: Callable) -> None:
    target.addEventListener(event_name, retain(callback))


def set_hidden(element_id: str, hidden: bool) -> None:
    element = by_id(element_id)
    if element is not None:
        element.hidden = hidden


def set_text(element_id: str, value: object) -> None:
    element = by_id(element_id)
    if element is not None:
        element.textContent = str(value)


def close_dialog(*_args) -> None:
    global DIALOG_CALLBACK
    DIALOG_CALLBACK = None
    set_hidden("app-dialog", True)


def open_dialog(
    title: str,
    message: str,
    callback: Callable[[str], bool] | None = None,
    *,
    initial: str = "",
    input_mode: bool = True,
    accept_label: str = "Aceptar",
    show_cancel: bool = True,
) -> None:
    global DIALOG_CALLBACK
    DIALOG_CALLBACK = callback
    set_text("app-dialog-title", title)
    set_text("app-dialog-message", message)
    by_id("app-dialog-message").classList.remove("error")
    dialog_input = by_id("app-dialog-input")
    dialog_input.hidden = not input_mode
    dialog_input.value = initial
    set_text("app-dialog-accept", accept_label)
    set_hidden("app-dialog-cancel", not show_cancel)
    set_hidden("app-dialog", False)
    if input_mode:
        dialog_input.focus()
        dialog_input.select()
    else:
        by_id("app-dialog-accept").focus()


def dialog_error(message: str) -> None:
    set_text("app-dialog-message", message)
    by_id("app-dialog-message").classList.add("error")
    by_id("app-dialog-input").focus()


def submit_dialog(*_args) -> None:
    callback = DIALOG_CALLBACK
    value = str(by_id("app-dialog-input").value)
    if callback is None or callback(value):
        close_dialog()


def on_dialog_keydown(event) -> None:
    if event.key == "Enter":
        event.preventDefault()
        submit_dialog()
    elif event.key == "Escape":
        close_dialog()


def notify(message: str) -> None:
    try:
        window.alert(message)
    except Exception:
        open_dialog("Aviso", message, input_mode=False, show_cancel=False)


def number_label(value: object) -> str:
    if isinstance(value, float):
        if math.isnan(value):
            return "NaN"
        if value.is_integer():
            return str(int(value))
        return f"{value:.8g}"
    return str(value)


def current_graph_json() -> dict[str, Any]:
    return STATE.tab.graph_data()


def algorithm_params() -> dict[str, Any]:
    """Parámetros propios del algoritmo activo, en el formato que espera el motor.

    Los toma de `ALGORITMOS` (valores por omisión) y del estado (lo que el usuario editó). No
    conoce ningún algoritmo por nombre.
    """
    guardados = STATE.algorithm_params.setdefault(STATE.algorithm, {})
    for nombre, (_, por_omision) in algoritmo_activo().get("parametros", {}).items():
        guardados.setdefault(nombre, por_omision)
    return dict(guardados)


def render_algorithm_options() -> None:
    """Puebla el selector con los algoritmos registrados."""
    opciones = "".join(
        f'<option value="{html.escape(clave)}">{html.escape(algoritmo["nombre"])}</option>'
        for clave, algoritmo in [*ALGORITMOS.items(), (CLAVE_USUARIO, RANURA_USUARIO)]
    )
    select = by_id("algorithm-select")
    if select.innerHTML != opciones:
        select.innerHTML = opciones
    select.value = STATE.algorithm


def render_user_code() -> None:
    """Muestra la caja de código sólo cuando la ranura de usuario está activa.

    Nunca **escribe** en el ``textarea``: `render_controls` corre en cada render
    y sobrescribirlo le quitaría a quien escribe lo que lleva tecleado. La
    plantilla inicial es el contenido del elemento en ``index.html``.
    """

    propio = STATE.algorithm == CLAVE_USUARIO
    set_hidden("user-algorithm", not propio)
    if propio and not STATE.user_source:
        STATE.user_source = str(by_id("user-code").value)


def on_user_code_change(event) -> None:
    STATE.user_source = str(event.target.value)


def show_user_code_error(mensaje: str) -> None:
    caja = by_id("user-code-error")
    caja.textContent = mensaje
    caja.hidden = False


def clear_user_code_error() -> None:
    by_id("user-code-error").hidden = True


def user_source() -> str | None:
    """El fuente que debe viajar en el mensaje, o ``None`` si no aplica."""

    if STATE.algorithm != CLAVE_USUARIO:
        return None
    return STATE.user_source or str(by_id("user-code").value)


def render_algorithm_params() -> None:
    """Genera los campos del algoritmo activo desde su declaración de parámetros.

    Solo reconstruye el marcado cuando cambia el algoritmo: regenerarlo en cada
    render pisaría lo que el usuario está escribiendo y le quitaría el foco al
    campo. Las etiquetas se insertan como HTML a propósito (vienen de
    ``algoritmos.py``, que es código del proyecto) para poder usar ``<code>``.
    """
    contenedor = by_id("algorithm-params")
    if str(contenedor.getAttribute("data-algorithm") or "") == STATE.algorithm:
        return

    parametros = algoritmo_activo().get("parametros", {})
    valores = algorithm_params()
    campos = "".join(
        f'<label class="field-label" for="param-{html.escape(nombre)}">{etiqueta}</label>'
        f'<input id="param-{html.escape(nombre)}" class="agreed-input" type="number" '
        f'step="any" value="{html.escape(str(valores[nombre]))}" '
        f'data-param="{html.escape(nombre)}" />'
        for nombre, (etiqueta, _) in parametros.items()
    )
    contenedor.innerHTML = campos
    contenedor.setAttribute("data-algorithm", STATE.algorithm)
    contenedor.hidden = not parametros


def graph_snapshot(tab: GraphTab | None = None) -> GraphTab:
    return (tab or STATE.tab).clone()


def mark_graph_changed() -> None:
    if STATE.simulation is None and STATE.tab.nodes:
        STATE.initial_graph = graph_snapshot()


def graph_nodes_by_id(tab: GraphTab | None = None) -> dict[str, Node]:
    return {node.id: node for node in (tab or STATE.tab).nodes}


def render_tabs() -> None:
    pieces: list[str] = []
    for tab in STATE.tabs:
        active = tab.id == STATE.active_tab_id
        close = ""
        if len(STATE.tabs) > 1:
            close = (
                f'<button class="tab-close" type="button" data-close-tab="{html.escape(tab.id)}" '
                'aria-label="Cerrar pestaña">✕</button>'
            )
        pieces.append(
            f'<div class="tab{" active" if active else ""}" role="tab" tabindex="0" '
            f'aria-selected="{str(active).lower()}" data-tab-id="{html.escape(tab.id)}">'
            f'<span class="tab-name">{html.escape(tab.name)}</span>{close}</div>'
        )
    pieces.append('<button id="new-tab-btn" class="tab-new" type="button">+ Nueva</button>')
    by_id("graph-tabs").innerHTML = "".join(pieces)


def edge_style(edge: Edge) -> tuple[str, float, str]:
    if edge.highlight == "updated":
        return "#F4B45C", 4, "edge-animated"
    if edge.highlight == "delivered":
        return "#7A6E9E", 4, "edge-animated"
    if edge.highlight == "quiet":
        return "#413A5E", 1, ""
    if edge.id == STATE.selected_edge_id:
        return "#6E7CF2", 4, ""
    return "#6C6690", 3, ""


def render_canvas() -> None:
    tab = STATE.tab
    nodes = graph_nodes_by_id(tab)
    transform = f"translate({STATE.pan_x:.3f} {STATE.pan_y:.3f}) scale({STATE.zoom:.5f})"
    for layer_id in ("edges-layer", "edge-labels-layer", "nodes-layer", "connection-layer"):
        by_id(layer_id).setAttribute("transform", transform)

    edge_parts: list[str] = []
    label_parts: list[str] = []
    for edge in tab.edges:
        source = nodes.get(edge.source)
        target = nodes.get(edge.target)
        if source is None or target is None:
            continue
        geometry = edge_geometry(source, target, tab.directed and has_reverse(edge, tab.edges))
        color, width, animation = edge_style(edge)
        marker = ' marker-end="url(#arrowhead)"' if tab.directed else ""
        edge_parts.append(
            f'<g class="graph-edge {animation}" data-edge-id="{html.escape(edge.id)}">'
            f'<path class="edge-hit" d="{geometry["path"]}" fill="none" stroke="transparent" stroke-width="16"></path>'
            f'<path class="edge-path" d="{geometry["path"]}" fill="none" stroke="{color}" '
            f'stroke-width="{width}"{marker}></path>'
            f'<circle cx="{geometry["source_x"]}" cy="{geometry["source_y"]}" r="4" fill="#ff6b6b" '
            'stroke="#fff" stroke-width="1.5"></circle>'
            f'<circle cx="{geometry["target_x"]}" cy="{geometry["target_y"]}" r="4" fill="#4ecdc4" '
            'stroke="#fff" stroke-width="1.5"></circle></g>'
        )
        raw_label = str(edge.weight)
        label = html.escape(raw_label)
        label_width = max(40.0, len(raw_label) * 7.4 + 14.0)
        lx, ly = geometry["label_x"], geometry["label_y"]
        label_parts.append(
            f'<g class="edge-label" data-edge-id="{html.escape(edge.id)}" '
            f'transform="translate({lx:.3f} {ly:.3f})">'
            f'<rect x="{-label_width/2:.2f}" y="-13" width="{label_width:.2f}" height="26" rx="4"></rect>'
            f'<text text-anchor="middle" dominant-baseline="middle">{label}</text></g>'
        )
    by_id("edges-layer").innerHTML = "".join(edge_parts)
    by_id("edge-labels-layer").innerHTML = "".join(label_parts)

    # El líder de un algoritmo que lo declara es el último nodo (ver `lider` en algoritmos.py).
    lider = tab.nodes[-1].id if algoritmo_activo().get("lider") and tab.nodes else None
    node_parts: list[str] = []
    for node in tab.nodes:
        fill, text_color = value_color(node.value)
        selected = " selected" if node.id == STATE.selected_node_id else ""
        figura = (
            f'<rect class="node-circle" x="3" y="3" width="54" height="54" rx="6" fill="{fill}" stroke="#100E1A" stroke-width="3"></rect>'
            if node.id == lider else
            f'<circle class="node-circle" cx="30" cy="30" r="30" fill="{fill}" stroke="#100E1A" stroke-width="3"></circle>'
        )
        node_parts.append(
            f'<g class="graph-node{selected}" data-node-id="{html.escape(node.id)}" '
            f'transform="translate({node.x:.3f} {node.y:.3f})">'
            f'{figura}'
            f'<text x="30" y="30" text-anchor="middle" dominant-baseline="middle" '
            f'fill="{text_color}">{html.escape(number_label(node.value))}</text>'
            f'<text class="node-name" x="30" y="76" text-anchor="middle">{html.escape(node.id)}</text></g>'
        )
    by_id("nodes-layer").innerHTML = "".join(node_parts)
    render_minimap()


def render_minimap() -> None:
    nodes = STATE.tab.nodes
    edges = STATE.tab.edges
    if not nodes:
        by_id("minimap-nodes").innerHTML = ""
        by_id("minimap-edges").innerHTML = ""
        return
    min_x = min(node.x for node in nodes)
    min_y = min(node.y for node in nodes)
    max_x = max(node.x + 60 for node in nodes)
    max_y = max(node.y + 60 for node in nodes)
    span_x = max(max_x - min_x, 120)
    span_y = max(max_y - min_y, 80)
    scale = min(158 / span_x, 88 / span_y)
    offset_x = 11 + (158 - span_x * scale) / 2
    offset_y = 11 + (88 - span_y * scale) / 2

    def point(node: Node) -> tuple[float, float]:
        return (
            offset_x + (node.x + 30 - min_x) * scale,
            offset_y + (node.y + 30 - min_y) * scale,
        )

    node_map = graph_nodes_by_id()
    edge_svg: list[str] = []
    for edge in edges:
        if edge.source in node_map and edge.target in node_map:
            sx, sy = point(node_map[edge.source])
            tx, ty = point(node_map[edge.target])
            edge_svg.append(f'<line x1="{sx:.2f}" y1="{sy:.2f}" x2="{tx:.2f}" y2="{ty:.2f}"></line>')
    node_svg = []
    for node in nodes:
        x, y = point(node)
        fill, _ = value_color(node.value)
        node_svg.append(f'<circle cx="{x:.2f}" cy="{y:.2f}" r="{max(3, 9*scale):.2f}" fill="{fill}"></circle>')
    by_id("minimap-edges").innerHTML = "".join(edge_svg)
    by_id("minimap-nodes").innerHTML = "".join(node_svg)

    canvas_bounds = by_id("graph-canvas").getBoundingClientRect()
    world_left = -STATE.pan_x / STATE.zoom
    world_top = -STATE.pan_y / STATE.zoom
    view_x = offset_x + (world_left - min_x) * scale
    view_y = offset_y + (world_top - min_y) * scale
    view_width = float(canvas_bounds.width) / STATE.zoom * scale
    view_height = float(canvas_bounds.height) / STATE.zoom * scale
    clipped_x = max(1.0, min(179.0, view_x))
    clipped_y = max(1.0, min(109.0, view_y))
    clipped_right = max(clipped_x, min(179.0, view_x + view_width))
    clipped_bottom = max(clipped_y, min(109.0, view_y + view_height))
    viewport = by_id("minimap-viewport")
    viewport.setAttribute("x", f"{clipped_x:.2f}")
    viewport.setAttribute("y", f"{clipped_y:.2f}")
    viewport.setAttribute("width", f"{max(0.0, clipped_right-clipped_x):.2f}")
    viewport.setAttribute("height", f"{max(0.0, clipped_bottom-clipped_y):.2f}")


#: Divisiones permitidas en el eje Y. Se elige la que desperdicie menos alto.
CHART_DIVISIONS = (4, 5, 6)

#: Pasos "redondos" admitidos, como mantisa. Se escalan por potencias de diez.
CHART_STEPS = (1.0, 2.0, 2.5, 5.0)


def nice_axis(value: float) -> tuple[float, int]:
    """Devuelve ``(tope, divisiones)`` para un eje con marcas legibles.

    Busca el paso de la forma ``{1, 2, 2.5, 5} × 10^k`` y el número de
    divisiones que cubran el dato desperdiciando el menor alto posible. Así las
    marcas caen en números redondos (0, 0.25, 0.5, 0.75, 1) en vez de en
    múltiplos del máximo observado, que producían cosas como 1.08 y 0.81.

    Dejar variar las divisiones importa: con 4 fijas, un máximo de 2.5 obligaba
    a un tope de 4 y la curva ocupaba media gráfica. Con 5 divisiones el tope
    es 2.5 exacto.

    El tope siempre cubre el dato, así que la curva nunca se sale. Un valor no
    positivo o no finito devuelve la escala en reposo, ``(1.0, 4)``.
    """

    if not math.isfinite(value) or value <= 0:
        return 1.0, 4

    mejor: tuple[float, int] | None = None
    exponente = math.floor(math.log10(value))
    for k in range(exponente - 2, exponente + 2):
        base = 10.0**k
        for mantisa in CHART_STEPS:
            paso = mantisa * base
            divisiones = math.ceil(value / paso - 1e-12)
            if divisiones not in CHART_DIVISIONS:
                continue
            tope = paso * divisiones
            # Menor tope = menos alto desperdiciado. A igualdad, menos marcas.
            if mejor is None or (tope, divisiones) < mejor:
                mejor = (tope, divisiones)

    return mejor if mejor is not None else (value, 4)


def axis_label(value: float) -> str:
    """Formatea una marca de eje sin ceros de relleno ni ruido de punto flotante."""

    if value == 0:
        return "0"
    if abs(value) >= 1e6 or abs(value) < 1e-4:
        return f"{value:.1e}"
    return f"{value:.6f}".rstrip("0").rstrip(".")


def render_chart_axis(max_y: float, divisions: int, top: float, bottom: float) -> None:
    """Dibuja rejilla y marcas de ambos ejes para la escala dada.

    La rejilla se genera junto con las etiquetas para que no puedan desfasarse:
    si el número de divisiones cambia, las líneas lo siguen.
    """

    grid = []
    labels = []
    for index in range(divisions + 1):
        fraction = index / divisions
        y = bottom - fraction * (bottom - top)
        grid.append(f'<line x1="44" y1="{y:.1f}" x2="382" y2="{y:.1f}"></line>')
        labels.append(
            f'<text x="38" y="{y+4:.1f}" text-anchor="end">{axis_label(max_y * fraction)}</text>'
        )
    labels.extend(
        [
            '<text x="44" y="311" text-anchor="middle">0</text>',
            '<text x="213" y="311" text-anchor="middle">0.5</text>',
            '<text x="382" y="311" text-anchor="middle">1</text>',
        ]
    )
    by_id("chart-grid").innerHTML = "".join(grid)
    by_id("chart-axis-labels").innerHTML = "".join(labels)


def render_chart(points: list[dict[str, float]]) -> None:
    global CHART_POINTS, CHART_MAX_Y
    CHART_POINTS = points
    set_hidden("chart-tooltip", True)
    left, right, top, bottom = 44.0, 382.0, 25.0, 293.0

    if points:
        values = [float(p["ciHigh"]) for p in points] + [float(p["mean"]) for p in points]
        max_y, divisions = nice_axis(max(values))
    else:
        max_y, divisions = 1.0, 4
    CHART_MAX_Y = max_y
    render_chart_axis(max_y, divisions, top, bottom)

    if not points:
        by_id("chart-confidence-band").setAttribute("d", "")
        by_id("chart-mean-line").setAttribute("d", "")
        return

    def x_coord(p: float) -> float:
        return left + max(0.0, min(1.0, p)) * (right - left)

    def y_coord(value: float) -> float:
        return bottom - (value / max_y) * (bottom - top)

    mean_path = " ".join(
        ("M" if index == 0 else "L") + f" {x_coord(float(point['p'])):.2f},{y_coord(float(point['mean'])):.2f}"
        for index, point in enumerate(points)
    )
    upper = [f"{x_coord(float(p['p'])):.2f},{y_coord(float(p['ciHigh'])):.2f}" for p in points]
    lower = [f"{x_coord(float(p['p'])):.2f},{y_coord(float(p['ciLow'])):.2f}" for p in reversed(points)]
    band_path = "M " + " L ".join(upper + lower) + " Z"
    by_id("chart-mean-line").setAttribute("d", mean_path)
    by_id("chart-confidence-band").setAttribute("d", band_path)


def on_chart_pointer_move(event) -> None:
    if not CHART_POINTS:
        return
    svg = by_id("sweep-chart-svg")
    bounds = svg.getBoundingClientRect()
    if float(bounds.width) <= 0 or float(bounds.height) <= 0:
        return
    svg_x = (float(event.clientX) - float(bounds.left)) * 400.0 / float(bounds.width)
    p_value = max(0.0, min(1.0, (svg_x - 44.0) / (382.0 - 44.0)))
    point = min(CHART_POINTS, key=lambda item: abs(float(item["p"]) - p_value))
    tooltip = by_id("chart-tooltip")
    tooltip.innerHTML = (
        f'<strong>p = {float(point["p"]):.3f}</strong><br>'
        f'Media: {float(point["mean"]):.4f}<br>'
        f'IC 95%: [{float(point["ciLow"]):.4f}, {float(point["ciHigh"]):.4f}]'
    )
    chart_bounds = tooltip.parentElement.getBoundingClientRect()
    local_x = float(event.clientX) - float(chart_bounds.left)
    local_y = float(event.clientY) - float(chart_bounds.top)
    tooltip.style.left = f"{min(local_x + 12.0, max(4.0, float(chart_bounds.width) - 176.0)):.1f}px"
    tooltip.style.top = f"{max(4.0, local_y - 68.0):.1f}px"
    tooltip.hidden = False


def hide_chart_tooltip(*_args) -> None:
    set_hidden("chart-tooltip", True)


def render_json() -> None:
    set_hidden("json-panel", not STATE.show_json)
    set_text("show-json-btn", "Ocultar JSON" if STATE.show_json else "Mostrar JSON")
    by_id("json-content").textContent = json.dumps(current_graph_json(), indent=2, ensure_ascii=False)


def render_controls() -> None:
    tab = STATE.tab
    directed = by_id("directed-toggle")
    directed.checked = tab.directed
    set_text(
        "directed-description",
        "→ Las aristas tienen dirección (con flechas)" if tab.directed else "— Las aristas no tienen dirección",
    )
    render_algorithm_options()
    render_algorithm_params()
    render_user_code()
    by_id("simulate-btn").disabled = not bool(tab.nodes) or STATE.round_running
    if not STATE.round_running:
        etiqueta = "Simular"
    elif STATE.engine_ready:
        etiqueta = "Simulando…"
    else:
        # La primera ronda espera a que el worker baje Pyodide y NumPy. Decirlo
        # evita que una espera larga y normal parezca la aplicación colgada.
        etiqueta = "Cargando motor…"
    set_text("simulate-btn", etiqueta)
    by_id("sweep-run-btn").disabled = not bool(tab.nodes) or STATE.sweep_running
    set_hidden("simulation-results", STATE.simulation is None)
    if STATE.simulation is not None:
        set_text("toggle-simulation-btn", "Ver Antes" if STATE.show_after else "Ver Después")
        by_id("simulation-status").innerHTML = (
            '<p class="result-active">✓ Mostrando resultado (aristas verdes = propagación)</p>'
            if STATE.show_after
            else "<p>Mostrando estado inicial</p>"
        )
    set_hidden("shift-indicator", not STATE.shift_pressed)
    by_id("canvas-container").classList.toggle("shift-mode", STATE.shift_pressed)
    by_id("view-editor").classList.toggle("active", STATE.active_view == "editor")
    by_id("view-analysis").classList.toggle("active", STATE.active_view == "analysis")
    by_id("view-editor").setAttribute("aria-selected", str(STATE.active_view == "editor").lower())
    by_id("view-analysis").setAttribute("aria-selected", str(STATE.active_view == "analysis").lower())
    set_hidden("sweep-panel", STATE.active_view != "analysis")


def render_all() -> None:
    render_tabs()
    render_controls()
    render_canvas()
    render_json()


def screen_to_world(client_x: float, client_y: float) -> tuple[float, float]:
    bounds = by_id("graph-canvas").getBoundingClientRect()
    local_x = float(client_x) - float(bounds.left)
    local_y = float(client_y) - float(bounds.top)
    return (local_x - STATE.pan_x) / STATE.zoom, (local_y - STATE.pan_y) / STATE.zoom


def add_node(x: float, y: float) -> None:
    node = Node(STATE.next_node_id(), 0, x - 30, y - 30)
    STATE.tab.nodes.append(node)
    STATE.selected_node_id = node.id
    STATE.selected_edge_id = None
    mark_graph_changed()
    render_all()


def add_edge(source_id: str, target_id: str) -> None:
    tab = STATE.tab
    if not tab.directed:
        duplicate = any(
            (edge.source == source_id and edge.target == target_id)
            or (edge.source == target_id and edge.target == source_id)
            for edge in tab.edges
        )
        if duplicate:
            return
    serial = sum(len(item.edges) for item in STATE.tabs)
    tab.edges.append(Edge(f"edge_local_{serial}_{STATE.node_counter}", source_id, target_id, "1"))
    mark_graph_changed()
    render_all()


def find_node(node_id: str) -> Node | None:
    return next((node for node in STATE.tab.nodes if node.id == node_id), None)


def find_edge(edge_id: str) -> Edge | None:
    return next((edge for edge in STATE.tab.edges if edge.id == edge_id), None)


def on_tabs_click(event) -> None:
    close_button = event.target.closest("[data-close-tab]")
    if close_button:
        close_tab(str(close_button.getAttribute("data-close-tab")))
        return
    new_button = event.target.closest("#new-tab-btn")
    if new_button:
        new_tab()
        return
    tab_button = event.target.closest("[data-tab-id]")
    if tab_button:
        STATE.active_tab_id = str(tab_button.getAttribute("data-tab-id"))
        STATE.selected_node_id = None
        STATE.selected_edge_id = None
        render_all()


def new_tab() -> None:
    tab = GraphTab(STATE.next_tab_id(), f"Grafo {len(STATE.tabs) + 1}")
    STATE.tabs.append(tab)
    STATE.active_tab_id = tab.id
    render_all()


def close_tab(tab_id: str) -> None:
    if len(STATE.tabs) == 1:
        notify("No puedes cerrar la última pestaña")
        return
    STATE.tabs = [tab for tab in STATE.tabs if tab.id != tab_id]
    if STATE.active_tab_id == tab_id:
        STATE.active_tab_id = STATE.tabs[0].id
    STATE.selected_node_id = None
    STATE.selected_edge_id = None
    render_all()


def on_drag_start(event) -> None:
    event.dataTransfer.setData("application/graph-node", "Nodo")
    event.dataTransfer.effectAllowed = "move"


def add_node_at_canvas_center(*_args) -> None:
    """Alternativa accesible al drag-and-drop conservando el mismo nodo inicial."""
    bounds = by_id("graph-canvas").getBoundingClientRect()
    x, y = screen_to_world(
        float(bounds.left) + float(bounds.width) / 2,
        float(bounds.top) + float(bounds.height) / 2,
    )
    add_node(x, y)


def on_drag_over(event) -> None:
    event.preventDefault()
    event.dataTransfer.dropEffect = "move"


def on_drop(event) -> None:
    event.preventDefault()
    if not event.dataTransfer.getData("application/graph-node"):
        return
    x, y = screen_to_world(event.clientX, event.clientY)
    add_node(x, y)


def on_canvas_pointer_down(event) -> None:
    node_element = event.target.closest("[data-node-id]")
    edge_element = event.target.closest("[data-edge-id]")
    if node_element:
        node_id = str(node_element.getAttribute("data-node-id"))
        STATE.selected_node_id = node_id
        STATE.selected_edge_id = None
        if STATE.shift_pressed or bool(event.shiftKey):
            CONNECTION.clear()
            CONNECTION.update({"source": node_id, "pointer": event.pointerId})
            by_id("graph-canvas").setPointerCapture(event.pointerId)
            event.preventDefault()
        else:
            node = find_node(node_id)
            if node is not None:
                x, y = screen_to_world(event.clientX, event.clientY)
                DRAG.clear()
                DRAG.update({
                    "node": node_id,
                    "dx": x - node.x,
                    "dy": y - node.y,
                    "start_client_x": float(event.clientX),
                    "start_client_y": float(event.clientY),
                    "pointer": event.pointerId,
                    "moved": False,
                })
                by_id("graph-canvas").setPointerCapture(event.pointerId)
        return
    if edge_element:
        STATE.selected_edge_id = str(edge_element.getAttribute("data-edge-id"))
        STATE.selected_node_id = None
        return
    STATE.selected_node_id = None
    STATE.selected_edge_id = None
    if STATE.space_pressed:
        PAN.clear()
        PAN.update({"x": event.clientX, "y": event.clientY, "pan_x": STATE.pan_x, "pan_y": STATE.pan_y})
        event.preventDefault()
    render_canvas()


def on_canvas_pointer_move(event) -> None:
    if DRAG:
        if not bool(DRAG["moved"]):
            distance = math.hypot(
                float(event.clientX) - float(DRAG["start_client_x"]),
                float(event.clientY) - float(DRAG["start_client_y"]),
            )
            # Un mouse real suele emitir pequeños movimientos entre los dos
            # clics. No inicies el drag ni reemplaces el SVG hasta superar un
            # umbral claro; de otro modo el navegador pierde el `dblclick`.
            if distance < 6.0:
                return
            DRAG["moved"] = True
        node = find_node(str(DRAG["node"]))
        if node is not None:
            x, y = screen_to_world(event.clientX, event.clientY)
            node.x = x - float(DRAG["dx"])
            node.y = y - float(DRAG["dy"])
            render_canvas()
        return
    if CONNECTION:
        source = find_node(str(CONNECTION["source"]))
        if source is not None:
            sx, sy = node_center(source)
            x, y = screen_to_world(event.clientX, event.clientY)
            by_id("connection-layer").innerHTML = (
                f'<path class="connection-line" d="M {sx:.2f},{sy:.2f} L {x:.2f},{y:.2f}"></path>'
            )
        return
    if PAN:
        STATE.pan_x = float(PAN["pan_x"]) + float(event.clientX) - float(PAN["x"])
        STATE.pan_y = float(PAN["pan_y"]) + float(event.clientY) - float(PAN["y"])
        render_canvas()


def on_canvas_pointer_up(event) -> None:
    if DRAG:
        node_id = str(DRAG.get("node"))
        moved = bool(DRAG.get("moved"))
        DRAG.clear()
        try:
            by_id("graph-canvas").releasePointerCapture(event.pointerId)
        except Exception:
            pass
        if moved:
            LAST_NODE_CLICK.clear()
            mark_graph_changed()
            render_all()
        else:
            now = float(window.performance.now())
            previous_id = str(LAST_NODE_CLICK.get("node", ""))
            elapsed = now - float(LAST_NODE_CLICK.get("time", -1000.0))
            distance = math.hypot(
                float(event.clientX) - float(LAST_NODE_CLICK.get("x", -1000.0)),
                float(event.clientY) - float(LAST_NODE_CLICK.get("y", -1000.0)),
            )
            if previous_id == node_id and elapsed <= 750.0 and distance <= 14.0:
                LAST_NODE_CLICK.clear()
                open_node_editor(node_id)
            else:
                LAST_NODE_CLICK.clear()
                LAST_NODE_CLICK.update({
                    "node": node_id,
                    "time": now,
                    "x": float(event.clientX),
                    "y": float(event.clientY),
                })
    if CONNECTION:
        source_id = str(CONNECTION.get("source"))
        target_element = document.elementFromPoint(event.clientX, event.clientY)
        target_node = target_element.closest("[data-node-id]") if target_element else None
        CONNECTION.clear()
        by_id("connection-layer").innerHTML = ""
        try:
            by_id("graph-canvas").releasePointerCapture(event.pointerId)
        except Exception:
            pass
        if target_node:
            add_edge(source_id, str(target_node.getAttribute("data-node-id")))
        else:
            render_canvas()
    PAN.clear()


def open_node_editor(node_id: str) -> None:
    if not bool(by_id("app-dialog").hidden):
        return
    node = find_node(node_id)
    if node is None:
        return

    def commit(value: str) -> bool:
        try:
            node.value = float(value)
        except ValueError:
            dialog_error("El valor debe ser un número válido")
            return False
        mark_graph_changed()
        render_all()
        return True

    open_dialog(
        "Editar valor del nodo",
        "Ingresa el valor para este nodo:",
        commit,
        initial=str(node.value),
    )


def on_canvas_double_click(event) -> None:
    node_element = event.target.closest("[data-node-id]")
    if not node_element:
        return
    LAST_NODE_CLICK.clear()
    open_node_editor(str(node_element.getAttribute("data-node-id")))


def on_canvas_click(event) -> None:
    edge_element = event.target.closest("[data-edge-id]")
    if not edge_element:
        return
    edge = find_edge(str(edge_element.getAttribute("data-edge-id")))
    if edge is None:
        return
    def commit(value: str) -> bool:
        try:
            parsed = float(value)
        except ValueError:
            parsed = -1
        if not (0 < parsed <= 1):
            dialog_error("El valor debe ser un número en el intervalo (0, 1]")
            return False
        edge.weight = value
        mark_graph_changed()
        render_all()
        return True

    open_dialog(
        "Editar peso de la arista",
        "Ingresa un peso o probabilidad en el intervalo (0, 1].",
        commit,
        initial=edge.weight,
    )


def on_wheel(event) -> None:
    event.preventDefault()
    old_zoom = STATE.zoom
    factor = 1.12 if event.deltaY < 0 else 1 / 1.12
    new_zoom = max(0.2, min(3.0, old_zoom * factor))
    bounds = by_id("graph-canvas").getBoundingClientRect()
    local_x = float(event.clientX) - float(bounds.left)
    local_y = float(event.clientY) - float(bounds.top)
    world_x = (local_x - STATE.pan_x) / old_zoom
    world_y = (local_y - STATE.pan_y) / old_zoom
    STATE.zoom = new_zoom
    STATE.pan_x = local_x - world_x * new_zoom
    STATE.pan_y = local_y - world_y * new_zoom
    render_canvas()


def zoom_by(factor: float) -> None:
    canvas = by_id("graph-canvas")
    bounds = canvas.getBoundingClientRect()
    center_x, center_y = float(bounds.width) / 2, float(bounds.height) / 2
    old_zoom = STATE.zoom
    new_zoom = max(0.2, min(3.0, old_zoom * factor))
    world_x = (center_x - STATE.pan_x) / old_zoom
    world_y = (center_y - STATE.pan_y) / old_zoom
    STATE.zoom = new_zoom
    STATE.pan_x = center_x - world_x * new_zoom
    STATE.pan_y = center_y - world_y * new_zoom
    render_canvas()


def fit_view(*_args) -> None:
    bounds = by_id("graph-canvas").getBoundingClientRect()
    STATE.zoom, STATE.pan_x, STATE.pan_y = fit_transform(STATE.tab.nodes, float(bounds.width), float(bounds.height))
    render_canvas()


def on_key_down(event) -> None:
    tag = str(document.activeElement.tagName).lower() if document.activeElement else ""
    editing = tag in {"input", "select", "textarea"}
    if event.key == "Shift" and not STATE.shift_pressed:
        STATE.shift_pressed = True
        render_controls()
    if event.code == "Space" and not editing:
        STATE.space_pressed = True
        event.preventDefault()
    if event.key in ("Delete", "Backspace") and not editing:
        if STATE.selected_node_id:
            node_id = STATE.selected_node_id
            STATE.tab.nodes = [node for node in STATE.tab.nodes if node.id != node_id]
            STATE.tab.edges = [edge for edge in STATE.tab.edges if edge.source != node_id and edge.target != node_id]
            STATE.selected_node_id = None
        elif STATE.selected_edge_id:
            STATE.tab.edges = [edge for edge in STATE.tab.edges if edge.id != STATE.selected_edge_id]
            STATE.selected_edge_id = None
        else:
            return
        mark_graph_changed()
        render_all()


def on_key_up(event) -> None:
    if event.key == "Shift":
        STATE.shift_pressed = False
        CONNECTION.clear()
        by_id("connection-layer").innerHTML = ""
        render_controls()
    if event.code == "Space":
        STATE.space_pressed = False
        PAN.clear()


def on_directed_change(event) -> None:
    directed = bool(event.target.checked)
    STATE.tab.directed = directed
    if not directed:
        seen: set[frozenset[str]] = set()
        unique: list[Edge] = []
        for edge in STATE.tab.edges:
            key = frozenset((edge.source, edge.target))
            if key in seen:
                continue
            seen.add(key)
            unique.append(edge)
        STATE.tab.edges = unique
    mark_graph_changed()
    render_all()


def show_json(*_args) -> None:
    STATE.show_json = not STATE.show_json
    render_json()


def clear_graph(*_args) -> None:
    try:
        if not window.confirm("¿Estás seguro de que quieres limpiar el grafo?"):
            return
    except Exception:
        open_dialog(
            "Limpiar grafo",
            "¿Estás seguro de que quieres limpiar el grafo?",
            lambda _value: clear_graph_confirmed(),
            input_mode=False,
            accept_label="Limpiar",
        )
        return
    clear_graph_confirmed()


def clear_graph_confirmed() -> bool:
    STATE.tab.nodes.clear()
    STATE.tab.edges.clear()
    STATE.clear_simulation()
    STATE.selected_node_id = None
    STATE.selected_edge_id = None
    render_all()
    return True


def export_json(*_args) -> None:
    content = json.dumps(current_graph_json(), indent=2, ensure_ascii=False)
    blob = Blob.new(
        to_js([content]),
        to_js({"type": "application/json"}, dict_converter=Object.fromEntries),
    )
    url = URL.createObjectURL(blob)
    link = document.createElement("a")
    link.href = url
    link.download = "graph.json"
    link.hidden = True
    document.body.appendChild(link)
    link.click()

    def cleanup():
        URL.revokeObjectURL(url)
        link.remove()

    window.setTimeout(retain(cleanup), 750)


def import_json_click(*_args) -> None:
    by_id("file-input").click()


def finish_import(text: str, filename: str) -> None:
    try:
        data = json.loads(text)
        if not isinstance(data.get("nodes"), list):
            notify('JSON inválido: debe contener un array de "nodes"')
            return
        nodes = []
        for item in data["nodes"]:
            nodes.append(Node.from_json(item, random.random() * 500, random.random() * 500))
        edges = [Edge.from_json(item, index) for index, item in enumerate(data.get("edges") or [])]
        tab = GraphTab(
            id=STATE.next_tab_id(),
            name=filename[:-5] if filename.lower().endswith(".json") else filename,
            nodes=nodes,
            edges=edges,
            directed=bool(data.get("directed", True)),
        )
        max_node = -1
        for node in nodes:
            if node.id.startswith("node_") and node.id[5:].isdigit():
                max_node = max(max_node, int(node.id[5:]))
        if max_node >= 0:
            STATE.node_counter = max(STATE.node_counter, max_node + 1)
        STATE.tabs.append(tab)
        STATE.active_tab_id = tab.id
        STATE.clear_simulation()
        render_all()
    except Exception as error:
        notify(f"Error al leer el archivo JSON: {error}")


def on_file_change(event) -> None:
    file = event.target.files.item(0) if event.target.files.length else None
    if not file:
        return

    def loaded(text):
        finish_import(str(text), str(file.name))

    callback = retain(loaded)
    file.text().then(callback)
    event.target.value = ""


def on_algorithm_change(event) -> None:
    STATE.algorithm = str(event.target.value)
    render_controls()
    render_canvas()  # el líder depende del algoritmo


def on_message_mode_change(event) -> None:
    STATE.message_mode = str(event.target.value)


def on_param_change(event) -> None:
    """Guarda el valor de un campo de parámetro del algoritmo activo.

    Delegado en el contenedor porque los campos se regeneran al cambiar de
    algoritmo y un listener por campo se perdería.
    """
    name = event.target.getAttribute("data-param") if event.target else None
    if not name:
        return
    try:
        valor = float(str(event.target.value))
    except ValueError:
        return
    algorithm_params()  # asegura que el diccionario del algoritmo exista
    STATE.algorithm_params[STATE.algorithm][str(name)] = valor


def apply_propagation(edges: list[Edge], propagation: list[dict[str, Any]]) -> None:
    for edge in edges:
        match = next(
            (
                item
                for item in propagation
                if str(item.get("source")) == edge.source and str(item.get("target")) == edge.target
            ),
            None,
        )
        if match is None:
            edge.highlight = "quiet"
        else:
            edge.highlight = "updated" if bool(match.get("updated_value")) else "delivered"


def simulate(*_args) -> None:
    """Mitad que manda: prepara el grafo, bloquea el boton y suelta el mensaje.

    Aqui termina la llamada. Lo que antes seguia despues de ``run_round`` vive
    ahora en ``apply_round``, que corre cuando el worker contesta.
    """

    if not STATE.tab.nodes:
        notify("No hay nodos para simular")
        return
    if STATE.round_running:
        return
    if ENGINE is None:
        notify("El motor de simulación no está disponible")
        return

    before = graph_snapshot(STATE.initial_graph or STATE.tab)
    if STATE.initial_graph is None:
        STATE.initial_graph = before.clone()
    STATE.round_before = before
    STATE.round_tab_id = STATE.tab.id
    STATE.round_running = True
    clear_user_code_error()
    render_controls()
    parametros: dict[str, Any] = {
        "graph": before.graph_data(),
        "algorithm": STATE.algorithm,
        "algorithmParams": algorithm_params(),
        "modo": STATE.message_mode,
    }
    fuente = user_source()
    if fuente is not None:
        parametros["fuente"] = fuente
    # Si el worker todavia esta cargando Pyodide, el mensaje espera ahi dentro:
    # su manejador hace `await runtimePromise` antes de atender nada. El plazo
    # se arma solo cuando el motor ya esta listo: contarlo durante la carga
    # mataria un worker que no tiene la culpa.
    ENGINE.postMessage(
        to_js(
            {"type": "ronda", "params": parametros},
            dict_converter=Object.fromEntries,
        )
    )
    if STATE.engine_ready:
        arm_round_timeout()


def apply_round(result: dict[str, Any]) -> None:
    """Mitad que recibe: aplica el resultado al snapshot que quedo esperando."""

    before = STATE.round_before
    if before is None:
        finish_round()
        return
    try:
        # La pestaña pudo cambiar mientras la ronda estaba en vuelo: hoy el
        # boton queda bloqueado, pero cambiar de pestaña no lo esta, y aplicar
        # el resultado sobre otra pestaña la corrompe en silencio.
        index = next(
            (
                position
                for position, tab in enumerate(STATE.tabs)
                if tab.id == STATE.round_tab_id
            ),
            None,
        )
        if index is None:
            return
        target = STATE.tabs[index]
        values = list(result["values"])
        after = before.clone()
        for position, node in enumerate(after.nodes):
            node.value = values[position]
        propagation = [dict(item) for item in result.get("propagation", [])]
        apply_propagation(after.edges, propagation)
        STATE.simulation = SimulationState(before, after, propagation)
        replacement = after.clone()
        replacement.id, replacement.name = target.id, target.name
        STATE.tabs[index] = replacement
        STATE.show_after = True
    except Exception as error:
        notify(f"Error al aplicar la simulación: {error}")
        window.console.error(error)
    finally:
        finish_round()


def finish_round() -> None:
    """Embudo: toda salida de la ronda pasa por aqui y desbloquea el boton.

    Va atado a «la ronda terminó», no a «se dibujó el canvas»: el error del
    worker no repinta nada y aun asi tiene que devolver el control.
    """

    cancel_round_timeout()
    STATE.round_running = False
    STATE.round_before = None
    STATE.round_tab_id = None
    render_all()


def arm_round_timeout() -> None:
    """Arma el plazo de la ronda. Sin esto, un algoritmo que no termina cuelga
    el botón para siempre: no llega ni resultado ni error, y no hay nada que
    desbloquee."""

    global ROUND_TIMER, ROUND_TIMEOUT_PROXY
    cancel_round_timeout()
    if ROUND_TIMEOUT_PROXY is None:
        ROUND_TIMEOUT_PROXY = retain(lambda *_args: on_round_timeout())
    ROUND_TIMER = window.setTimeout(ROUND_TIMEOUT_PROXY, ROUND_TIMEOUT_MS)


def cancel_round_timeout() -> None:
    global ROUND_TIMER
    if ROUND_TIMER is not None:
        window.clearTimeout(ROUND_TIMER)
        ROUND_TIMER = None


def on_round_timeout() -> None:
    """Mata el motor y devuelve el control. Es la protección que se gana al
    sacar la ejecución del hilo principal: ahí un ciclo infinito congela la
    pestaña y no queda nada por hacer."""

    if not STATE.round_running:
        return
    segundos = ROUND_TIMEOUT_MS // 1000
    mensaje = (
        f"El algoritmo no terminó en {segundos} s y se detuvo el motor.\n"
        "Suele ser un ciclo que no cierra."
    )
    if STATE.algorithm == CLAVE_USUARIO:
        show_user_code_error(mensaje)
    else:
        notify(mensaje)
    restart_engine_worker()
    finish_round()


def report_engine_error(message: dict[str, Any]) -> None:
    """Manda el fallo a donde se pueda hacer algo con él.

    Con código pegado, el traceback **es** la información útil: dice la línea.
    Con los algoritmos de casa sería ruido de Pyodide, así que ahí sigue yendo
    el mensaje corto por `notify`.
    """

    traza = message.get("traza")
    resumen = str(message.get("message") or "error desconocido")
    if not traza:
        notify(f"Error al ejecutar la simulación: {resumen}")
        return
    encabezado = (
        "Tu algoritmo no compila:"
        if str(message.get("etapa")) == "compilar"
        else "Tu algoritmo falló al correr:"
    )
    show_user_code_error(f"{encabezado}\n\n{trim_traceback(str(traza), resumen)}")


def trim_traceback(traza: str, resumen: str) -> str:
    """Deja el traceback a partir del primer marco del código pegado.

    Lo de arriba son marcos de Pyodide, de `runPythonAsync` y de `usuario.py`:
    ruido para quien escribió el algoritmo, que solo necesita su número de línea.
    El fuente se compila con el nombre `<algoritmo>` justamente para poder
    encontrar aquí dónde empieza lo suyo.

    Si no aparece ningún marco propio, el fallo es nuestro o del contrato (por
    ejemplo, código que no registra nada): ahí el mensaje corto dice más que un
    traceback de tripas ajenas.
    """

    lineas = traza.splitlines()
    # `File "<algoritmo>"` y no solo `"<algoritmo>"`: la propia línea de
    # `usuario.py` que llama a `compile` contiene esa cadena, y buscarla suelta
    # empezaba el recorte un marco antes.
    marco = 'File "<algoritmo>"'
    inicio = next((i for i, linea in enumerate(lineas) if marco in linea), None)
    if inicio is None:
        return resumen
    return "\n".join(["Traceback (llamada más reciente al final):", *lineas[inicio:]])


def restart_engine_worker() -> None:
    """Destruye el motor y levanta uno nuevo.

    El código de usuario viaja en cada mensaje, así que el worker nuevo no
    necesita que se le reinstale nada: el siguiente clic lo lleva consigo.
    """

    global ENGINE
    if ENGINE is not None:
        ENGINE.terminate()
        ENGINE = None
    STATE.engine_ready = False
    start_engine_worker()


def start_engine_worker() -> None:
    """Levanta el worker de rondas al cargar la pagina.

    Arrancarlo aqui y no en el primer clic evita que la primera simulacion
    espere a que Pyodide y NumPy carguen en un segundo runtime. Si alguien pica
    Simular antes de que termine de cargar, el mensaje espera dentro del worker:
    su manejador hace ``await`` sobre el runtime antes de atender nada.
    """

    global ENGINE
    ENGINE = Worker.new(
        "./js/sweep-worker.js",
        to_js({"type": "module"}, dict_converter=Object.fromEntries),
    )

    def on_message(event):
        data = event.data.to_py() if hasattr(event.data, "to_py") else event.data
        message = dict(data)
        kind = str(message.get("type", ""))
        if kind == "listo":
            STATE.engine_ready = True
            # Si alguien picó Simular durante la carga, el plazo empieza a
            # contar ahora: antes habría castigado a la descarga, no al código.
            if STATE.round_running:
                arm_round_timeout()
            render_controls()
        elif kind == "ronda":
            apply_round(json.loads(str(message.get("resultado") or "{}")))
        elif kind == "error":
            report_engine_error(message)
            finish_round()

    def on_error(event):
        notify(f"Error en el motor de simulación: {event.message}")
        finish_round()

    ENGINE.addEventListener("message", retain(on_message))
    ENGINE.addEventListener("error", retain(on_error))


def toggle_simulation(*_args) -> None:
    if STATE.simulation is None:
        return
    source = STATE.simulation.before if STATE.show_after else STATE.simulation.after
    replacement = source.clone()
    replacement.id, replacement.name = STATE.tab.id, STATE.tab.name
    if not STATE.show_after:
        apply_propagation(replacement.edges, STATE.simulation.propagation)
    STATE.tabs[STATE.tabs.index(STATE.tab)] = replacement
    STATE.show_after = not STATE.show_after
    render_all()


def reset_simulation(*_args) -> None:
    if STATE.initial_graph is not None:
        replacement = STATE.initial_graph.clone()
        replacement.id, replacement.name = STATE.tab.id, STATE.tab.name
        STATE.tabs[STATE.tabs.index(STATE.tab)] = replacement
    STATE.simulation = None
    STATE.show_after = False
    render_all()


def change_view(view: str) -> None:
    STATE.active_view = view
    render_controls()


def positive_int(element_id: str, minimum: int) -> int:
    try:
        return max(minimum, int(float(str(by_id(element_id).value))))
    except ValueError:
        return minimum


def set_sweep_inputs_disabled(disabled: bool) -> None:
    for element_id in ("p-points-input", "simulations-input", "iterations-input", "rounds-input"):
        by_id(element_id).disabled = disabled
    set_hidden("sweep-run-btn", disabled)
    set_hidden("sweep-stop-btn", not disabled)


def stop_sweep(*_args) -> None:
    global WORKER
    if WORKER is not None:
        WORKER.terminate()
        WORKER = None
    STATE.sweep_running = False
    set_sweep_inputs_disabled(False)
    by_id("sweep-run-btn").disabled = not bool(STATE.tab.nodes)
    by_id("sweep-status").innerHTML = "<p>Ajusta los parámetros y ejecuta el sweep.</p>"


def start_sweep(*_args) -> None:
    global WORKER
    if not STATE.tab.nodes or STATE.sweep_running:
        return
    params = {
        "graph": current_graph_json(),
        "algorithm": STATE.algorithm,
        # Nombre explícito: este dict ya se llama `params` a nivel del mensaje,
        # y esta clave son los parámetros del algoritmo, no los del sweep.
        "algorithmParams": algorithm_params(),
        "modo": STATE.message_mode,
        "fuente": user_source(),
        "numPPoints": positive_int("p-points-input", 2),
        "totalSimulationsPerP": positive_int("simulations-input", 1),
        "iterations": positive_int("iterations-input", 1),
        "rounds": positive_int("rounds-input", 1),
    }
    STATE.sweep_running = True
    set_sweep_inputs_disabled(True)
    render_chart([])
    by_id("sweep-status").innerHTML = "<p>Cargando motor Python del sweep…</p>"
    WORKER = Worker.new(
        "./js/sweep-worker.js",
        to_js({"type": "module"}, dict_converter=Object.fromEntries),
    )

    def on_message(event):
        data = event.data.to_py() if hasattr(event.data, "to_py") else event.data
        message = dict(data)
        kind = str(message.get("type", ""))
        if kind == "iteration":
            points = [dict(point) for point in message.get("points", [])]
            render_chart(points)
            completed = " — completo" if message.get("done") else ""
            by_id("sweep-status").innerHTML = (
                f'<p>Iteración {int(message["iteration"])+1}/{int(message["totalIterations"])} — '
                f'{int(message["simulationsPerP"])} sims/p{completed}</p>'
            )
        elif kind == "end":
            finish_sweep()
        elif kind == "error":
            by_id("sweep-status").innerHTML = f'<p class="sweep-error">Error: {html.escape(str(message.get("message")))}</p>'
            # El traceback del código pegado se queda en el panel del editor,
            # que es donde está la caja de texto que hay que corregir.
            if message.get("traza"):
                report_engine_error(message)
            finish_sweep()
        elif kind in {"loading", "start"}:
            if kind == "loading":
                by_id("sweep-status").innerHTML = "<p>Cargando Python y NumPy en el worker…</p>"

    def on_error(event):
        by_id("sweep-status").innerHTML = f'<p class="sweep-error">Error: {html.escape(str(event.message))}</p>'
        finish_sweep()

    WORKER.addEventListener("message", retain(on_message))
    WORKER.addEventListener("error", retain(on_error))
    WORKER.postMessage(to_js({"type": "run", "params": params}, dict_converter=Object.fromEntries))


def finish_sweep() -> None:
    global WORKER
    if WORKER is not None:
        WORKER.terminate()
        WORKER = None
    STATE.sweep_running = False
    set_sweep_inputs_disabled(False)
    by_id("sweep-run-btn").disabled = not bool(STATE.tab.nodes)


def bind_events() -> None:
    canvas = by_id("graph-canvas")
    listen(by_id("graph-tabs"), "click", on_tabs_click)
    listen(by_id("node-template"), "dragstart", on_drag_start)
    listen(by_id("node-template"), "click", add_node_at_canvas_center)
    listen(canvas, "dragover", on_drag_over)
    listen(canvas, "drop", on_drop)
    listen(canvas, "pointerdown", on_canvas_pointer_down)
    listen(canvas, "pointermove", on_canvas_pointer_move)
    listen(canvas, "pointerup", on_canvas_pointer_up)
    listen(canvas, "pointercancel", on_canvas_pointer_up)
    listen(canvas, "dblclick", on_canvas_double_click)
    listen(canvas, "click", on_canvas_click)
    listen(canvas, "wheel", on_wheel)
    listen(window, "keydown", on_key_down)
    listen(window, "keyup", on_key_up)
    listen(window, "resize", lambda _event: render_canvas())
    listen(by_id("directed-toggle"), "change", on_directed_change)
    listen(by_id("show-json-btn"), "click", show_json)
    listen(by_id("close-json-btn"), "click", show_json)
    listen(by_id("import-btn"), "click", import_json_click)
    listen(by_id("file-input"), "change", on_file_change)
    listen(by_id("export-btn"), "click", export_json)
    listen(by_id("clear-btn"), "click", clear_graph)
    listen(by_id("algorithm-select"), "change", on_algorithm_change)
    listen(by_id("message-mode-select"), "change", on_message_mode_change)
    listen(by_id("algorithm-params"), "change", on_param_change)
    listen(by_id("algorithm-params"), "input", on_param_change)
    listen(by_id("user-code"), "input", on_user_code_change)
    listen(by_id("simulate-btn"), "click", simulate)
    listen(by_id("toggle-simulation-btn"), "click", toggle_simulation)
    listen(by_id("reset-simulation-btn"), "click", reset_simulation)
    listen(by_id("view-editor"), "click", lambda _event: change_view("editor"))
    listen(by_id("view-analysis"), "click", lambda _event: change_view("analysis"))
    listen(by_id("zoom-in-btn"), "click", lambda _event: zoom_by(1.2))
    listen(by_id("zoom-out-btn"), "click", lambda _event: zoom_by(1 / 1.2))
    listen(by_id("fit-view-btn"), "click", fit_view)
    listen(by_id("sweep-run-btn"), "click", start_sweep)
    listen(by_id("sweep-stop-btn"), "click", stop_sweep)
    listen(by_id("sweep-chart-svg"), "pointermove", on_chart_pointer_move)
    listen(by_id("sweep-chart-svg"), "pointerleave", hide_chart_tooltip)
    listen(by_id("app-dialog-cancel"), "click", close_dialog)
    listen(by_id("app-dialog-accept"), "click", submit_dialog)
    listen(by_id("app-dialog-input"), "keydown", on_dialog_keydown)


def main() -> None:
    bind_events()
    start_engine_worker()
    render_all()
    fit_view()
    window.graphSimulatorReady = True


if __name__ == "__main__":
    main()
