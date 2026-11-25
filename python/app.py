"""Aplicación client-side: estado, DOM y editor SVG controlados desde Python."""

from __future__ import annotations

import asyncio
import html
import inspect
import json
import math
import random
import re
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Callable

from js import Blob, Object, URL, Worker, document, window
from pyodide.ffi import create_proxy, to_js

from geometry import edge_geometry, fit_transform, has_reverse, node_center, value_color
from models import AppState, Edge, GraphTab, Node, SimulationState
from simulation import algoritmos as modulo_algoritmos
from simulation.algoritmos import ALGORITMOS
from simulation.usuario import CLAVE as CLAVE_USUARIO


STATE = AppState()
PROXIES: list[Any] = []
# Workers distintos a proposito: los del barrido se terminan al cancelar
# y ENGINE tiene que sobrevivir a eso para seguir atendiendo al editor.
# shortcut: un Worker (con su Pyodide) por algoritmo palomeado, todos a la vez;
# si la memoria aprieta, turnarlos dentro de uno solo.
WORKERS: dict[str, Any] = {}
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
#: El código de ejemplo con que arranca el editor (el de index.html). Mientras el
#: textarea lo conserve, «Editar una copia» lo reemplaza sin preguntar.
PLANTILLA_USUARIO = ""
#: Un solo proxy para devolver «Copiar» a su texto: uno por clic haría crecer PROXIES.
COPY_RESET = None
#: Desglose por rondas: el número del último pedido al Worker (las respuestas
#: viejas se descartan) y la última simulación recibida.
TRACE_PEDIDO = 0
TRACE: dict[str, Any] | None = None
CHART_MAX_Y = 1.0
#: Diálogo «Topologías…»: la elegida (None mientras se ve la lista de botones) y lo
#: escrito en sus campos, como texto: `topologias.leer_parametros` lo valida.
TOPOLOGY: dict[str, Any] = {"clave": None, "valores": {}}


# La ranura del algoritmo propio sale en el selector junto a los de casa, pero
# su código solo existe dentro del Worker (`simulation.usuario`).
RANURA_USUARIO = {"nombre": "Algoritmo propio…"}

#: Lo que se explica bajo el selector de paso de mensajes (los modos del motor).
MODOS_AYUDA = {
    "por_arista": "Cada arista sortea por separado si entrega su mensaje (con probabilidad p). "
                  "En un grafo no dirigido, a→b y b→a comparten el mismo sorteo.",
    "por_emisor": "Cada nodo sortea una sola vez por ronda y ese sorteo vale para todas sus aristas. "
                  "Con pesos iguales, como en el sweep, su mensaje llega a todos sus vecinos o a ninguno.",
}

#: La mayor semilla que acepta `np.random.seed`.
SEED_MAX = 2**32 - 1

#: (mínimo, máximo) de cada campo del sweep. Son los límites de `iter_sweep`, y los
#: mismos que los min/max de index.html.
SWEEP_LIMITS = {
    "p-points-input": (2, 1001),
    "simulations-input": (1, 50_000),
    "iterations-input": (1, 1000),
    "rounds-input": (1, 1000),
}


def entrada_de(clave: str) -> dict[str, Any]:
    return RANURA_USUARIO if clave == CLAVE_USUARIO else ALGORITMOS[clave]


def algoritmo_activo() -> dict[str, Any]:
    return entrada_de(STATE.algorithm)


def claves_visibles() -> list[str]:
    """Los algoritmos en juego en esta vista: los palomeados en el análisis; si
    no, el del selector (el editor y «Código» trabajan con uno solo)."""
    return list(STATE.sweep_algorithms) if STATE.active_view == "analysis" else [STATE.algorithm]


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


def algorithm_params(clave: str | None = None) -> dict[str, Any]:
    """Parámetros propios de un algoritmo (el activo, si no se dice), en el formato del motor.

    Los toma de `ALGORITMOS` (valores por omisión) y del estado (lo que el usuario editó). No
    conoce ningún algoritmo por nombre.
    """
    clave = clave or STATE.algorithm
    guardados = STATE.algorithm_params.setdefault(clave, {})
    for nombre, (_, por_omision) in entrada_de(clave).get("parametros", {}).items():
        guardados.setdefault(nombre, por_omision)
    return dict(guardados)


def render_algorithm_options() -> None:
    """Puebla el selector y las casillas del análisis con los algoritmos registrados."""
    todos = [*ALGORITMOS.items(), (CLAVE_USUARIO, RANURA_USUARIO)]
    opciones = "".join(
        f'<option value="{html.escape(clave)}">{html.escape(algoritmo["nombre"])}</option>'
        for clave, algoritmo in todos
    )
    select = by_id("algorithm-select")
    if select.innerHTML != opciones:
        select.innerHTML = opciones
    select.value = STATE.algorithm
    casillas = "".join(
        f'<label class="checkbox-label"><input type="checkbox" data-algoritmo="{html.escape(clave)}"'
        f'{" checked" if clave in STATE.sweep_algorithms else ""} />'
        f'<span>{html.escape(algoritmo["nombre"])}</span></label>'
        for clave, algoritmo in todos
    )
    checks = by_id("algorithm-checks")
    if checks.innerHTML != casillas:
        checks.innerHTML = casillas


def on_algorithm_check(event) -> None:
    """Palomear o despalomear un algoritmo del sweep. Delegado: las casillas se regeneran."""
    clave = event.target.getAttribute("data-algoritmo") if event.target else None
    if not clave:
        return
    marcadas = set(STATE.sweep_algorithms) - {str(clave)}
    if event.target.checked:
        marcadas.add(str(clave))
    # En el orden del registro, no en el de los clics.
    STATE.sweep_algorithms = [c for c in [*ALGORITMOS, CLAVE_USUARIO] if c in marcadas]
    render_controls()
    # Las casillas se regeneraron: quien usa el teclado no debe perder su lugar.
    casilla = by_id("algorithm-checks").querySelector(f'[data-algoritmo="{clave}"]')
    if casilla is not None:
        casilla.focus()


def render_user_code() -> None:
    """Muestra la caja de código sólo cuando la ranura de usuario está activa.

    Nunca **escribe** en el ``textarea``: `render_controls` corre en cada render
    y sobrescribirlo le quitaría a quien escribe lo que lleva tecleado. La
    plantilla inicial es el contenido del elemento en ``index.html``.
    """

    propio = CLAVE_USUARIO in claves_visibles()
    # En «Código» la caja solo tendría el error: sin error quedaría un marco vacío.
    sin_nada = STATE.active_view == "code" and by_id("user-code-error").hidden
    set_hidden("user-algorithm", not propio or sin_nada)
    if propio and not STATE.user_source:
        STATE.user_source = str(by_id("user-code").value)


def on_user_code_change(event) -> None:
    STATE.user_source = str(event.target.value)


def show_user_code_error(mensaje: str) -> None:
    caja = by_id("user-code-error")
    caja.textContent = mensaje
    caja.hidden = False
    set_hidden("user-algorithm", False)


def clear_user_code_error() -> None:
    by_id("user-code-error").hidden = True
    render_user_code()


def user_source(clave: str | None = None) -> str | None:
    """El fuente que debe viajar en el mensaje, o ``None`` si no aplica."""

    if (clave or STATE.algorithm) != CLAVE_USUARIO:
        return None
    return STATE.user_source or str(by_id("user-code").value)


#: Las claves de una entrada de ALGORITMOS que son funciones, en el orden en que
#: se escriben en el `ALGORITMO` copiado.
CLAVES_FUNCION = ("ronda", "batch", "actualizo", "esperanza")


def codigo_de(clave: str) -> str:
    """El código de un algoritmo de casa, listo para pegarse como algoritmo propio.

    Junta las funciones de su entrada (`ronda`, `batch`, `actualizo`) y, de forma
    transitiva, las funciones de `algoritmos.py` que esas llaman (p. ej. `ciclo`
    usa `_regular` y `_binarios`), en el orden del archivo, y cierra con el
    diccionario `ALGORITMO`. `parametros` no se copia: el algoritmo propio recibe
    `params` vacío y cada `params.get(...)` usa su valor por omisión.
    """
    entrada = ALGORITMOS[clave]
    funciones: dict[str, Any] = {}
    pendientes = [entrada[k] for k in CLAVES_FUNCION if k in entrada]
    for funcion in pendientes:
        # getsource de una lambda devuelve la línea del diccionario, no una función.
        if funcion.__name__ == "<lambda>" or funcion.__module__ != modulo_algoritmos.__name__:
            raise ValueError(
                f"«{entrada['nombre']}» no se puede copiar: sus funciones deben ser `def` de algoritmos.py."
            )
    while pendientes:
        funcion = pendientes.pop()
        if funcion.__name__ in funciones:
            continue
        funciones[funcion.__name__] = funcion
        for nombre in funcion.__code__.co_names:
            usada = getattr(modulo_algoritmos, nombre, None)
            if inspect.isfunction(usada) and usada.__module__ == modulo_algoritmos.__name__:
                pendientes.append(usada)

    cabecera = [f"# Copia de «{entrada['nombre']}», de python/simulation/algoritmos.py."]
    if entrada.get("parametros"):
        nombres = ", ".join(
            f"{nombre} = {por_omision!r}" for nombre, (_, por_omision) in entrada["parametros"].items()
        )
        cabecera.append(
            f"# Aquí `params` llega vacío: usa su valor por omisión ({nombres}). Cámbialo en el código."
        )
    cuerpo = [
        inspect.getsource(funcion)
        for funcion in sorted(funciones.values(), key=lambda f: f.__code__.co_firstlineno)
    ]
    claves = [f'"nombre": {json.dumps(entrada["nombre"] + " (copia)", ensure_ascii=False)}']
    for k, v in entrada.items():
        if k in CLAVES_FUNCION:
            claves.append(f'"{k}": {v.__name__}')
        elif k not in ("nombre", "parametros"):
            claves.append(f'"{k}": {v!r}')
    return "\n".join(cabecera) + "\n\n\n" + "\n\n".join(cuerpo) + "\n\nALGORITMO = {" + ", ".join(claves) + "}\n"


def render_code_view() -> None:
    """La pestaña «Código»: el editor si el algoritmo es propio, si no su código."""
    propio = STATE.algorithm == CLAVE_USUARIO
    set_hidden("user-code-panel", not propio)
    set_hidden("builtin-code", propio)
    if propio:
        set_text("code-subtitle", "Tu algoritmo propio: se usa al simular y al correr el sweep "
                                  "mientras esté elegido en el selector.")
        return
    set_text("code-subtitle", f"«{algoritmo_activo()['nombre']}», de python/simulation/algoritmos.py. "
                              "Solo lectura: para cambiarlo, edita una copia.")
    codigo = by_id("builtin-code-text")
    if codigo.getAttribute("data-clave") == STATE.algorithm:
        return  # ya está puesto: render_controls corre seguido y el texto es largo
    try:
        texto, copiable = codigo_de(STATE.algorithm), True
    except ValueError as error:
        texto, copiable = str(error), False
    codigo.textContent = texto
    codigo.setAttribute("data-clave", STATE.algorithm)
    by_id("edit-copy-btn").disabled = not copiable
    by_id("copy-code-btn").disabled = not copiable


def copy_code(*_args) -> None:
    try:
        window.navigator.clipboard.writeText(codigo_de(STATE.algorithm))
    except Exception:
        notify("No se pudo copiar al portapapeles. Selecciona el código y cópialo a mano.")
        return
    set_text("copy-code-btn", "Copiado ✓")
    window.setTimeout(COPY_RESET, 1500)


def edit_copy(*_args) -> None:
    """Pone una copia del algoritmo activo en el editor y elige «Algoritmo propio»."""
    clave = STATE.algorithm
    fuente = codigo_de(clave)

    def aplicar(_valor: str = "") -> bool:
        by_id("user-code").value = fuente
        STATE.user_source = fuente
        STATE.algorithm = CLAVE_USUARIO
        clear_user_code_error()
        render_controls()
        render_canvas()  # el líder depende del algoritmo
        return True

    actual = str(by_id("user-code").value)
    if actual.strip() and actual != PLANTILLA_USUARIO:
        open_dialog(
            "Reemplazar tu algoritmo",
            f"Tu algoritmo propio tiene cambios. ¿Reemplazarlo por una copia de "
            f"«{ALGORITMOS[clave]['nombre']}»? Lo que escribiste se pierde.",
            aplicar,
            input_mode=False,
            accept_label="Reemplazar",
        )
        return
    aplicar()


def render_algorithm_params() -> None:
    """Genera los campos de los algoritmos en juego desde su declaración de parámetros.

    Solo reconstruye el marcado cuando cambian los algoritmos: regenerarlo en cada
    render pisaría lo que el usuario está escribiendo y le quitaría el foco al
    campo. Las etiquetas se insertan como HTML a propósito (vienen de
    ``algoritmos.py``, que es código del proyecto) para poder usar ``<code>``.
    """
    contenedor = by_id("algorithm-params")
    claves = claves_visibles()
    firma = "|".join(claves)
    if str(contenedor.getAttribute("data-algorithm") or "") == firma:
        return

    con_campos = [clave for clave in claves if entrada_de(clave).get("parametros")]
    campos = []
    for clave in con_campos:
        valores = algorithm_params(clave)
        # Con varios algoritmos a la vez, cada campo dice de cuál es.
        prefijo = f'{html.escape(entrada_de(clave)["nombre"])} · ' if len(con_campos) > 1 else ""
        for nombre, (etiqueta, _) in entrada_de(clave)["parametros"].items():
            campo = html.escape(f"param-{clave}-{nombre}")
            campos.append(
                f'<label class="field-label" for="{campo}">{prefijo}{etiqueta}</label>'
                f'<input id="{campo}" class="agreed-input" type="number" '
                f'step="any" value="{html.escape(str(valores[nombre]))}" '
                f'data-clave="{html.escape(clave)}" data-param="{html.escape(nombre)}" />'
            )
    contenedor.innerHTML = "".join(campos)
    contenedor.setAttribute("data-algorithm", firma)
    contenedor.hidden = not campos


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
    # Variables de app.css y no literales: así las aristas siguen al tema.
    if edge.highlight == "updated":
        return "var(--v1)", 4, "edge-animated"
    if edge.highlight == "delivered":
        return "var(--muted)", 4, "edge-animated"
    if edge.highlight == "quiet":
        return "var(--line)", 1, ""
    if edge.id == STATE.selected_edge_id:
        return "var(--v0)", 4, ""
    return "var(--faint)", 3, ""


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
        fill, text_color = value_color(node.value, STATE.theme)
        selected = " selected" if node.id == STATE.selected_node_id else ""
        figura = (
            f'<rect class="node-circle" x="3" y="3" width="54" height="54" rx="6" fill="{fill}" stroke="var(--page)" stroke-width="3"></rect>'
            if node.id == lider else
            f'<circle class="node-circle" cx="30" cy="30" r="30" fill="{fill}" stroke="var(--page)" stroke-width="3"></circle>'
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
        fill, _ = value_color(node.value, STATE.theme)
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

#: Área de trazado dentro del viewBox de #sweep-chart-svg (800 de ancho). El eje X,
#: sus marcas y la rejilla vertical están fijos en index.html con estos mismos números.
CHART_WIDTH = 800.0
CHART_LEFT, CHART_RIGHT, CHART_TOP, CHART_BOTTOM = 56.0, 776.0, 24.0, 392.0


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
        grid.append(f'<line x1="{CHART_LEFT}" y1="{y:.1f}" x2="{CHART_RIGHT}" y2="{y:.1f}"></line>')
        labels.append(
            f'<text x="{CHART_LEFT - 8}" y="{y+4:.1f}" text-anchor="end">{axis_label(max_y * fraction)}</text>'
        )
    by_id("chart-grid").innerHTML = "".join(grid)
    by_id("chart-axis-labels").innerHTML = "".join(labels)


def theory_for(experiment: dict[str, Any] | None) -> list[float] | None:
    """La curva exacta de un experimento, si existe y corresponde a sus puntos."""
    teorica = experiment.get("teorica") if experiment else None
    return teorica if teorica and len(teorica) == len(experiment["puntos"]) else None


def bound_curve() -> tuple[list[dict[str, float]], list[float]] | None:
    """``(puntos, cota)`` de la cota inferior por validez (teoria.cota_validez), si
    la red del sweep es un ciclo con entrada 0/1. No depende del algoritmo, así que
    basta la del primero que la traiga."""
    for _clave, e in chart_series():
        cota = e.get("cota")
        if cota and len(cota) == len(e["puntos"]):
            return e["puntos"], cota
    return None


#: Un color por algoritmo, en el orden del registro: el color sigue al algoritmo,
#: no a cuántos estén palomeados. Paleta categórica validada (daltonismo y
#: contraste) sobre el fondo de la gráfica.
CURVE_COLORS = ("#3987e5", "#d95926", "#199e70", "#c98500", "#d55181")


def curve_color(clave: str) -> str:
    # shortcut: 5 colores; con un sexto algoritmo se repiten, y ahí toca ampliar la paleta.
    orden = [*ALGORITMOS, CLAVE_USUARIO]
    return CURVE_COLORS[orden.index(clave) % len(CURVE_COLORS)] if clave in orden else CURVE_COLORS[0]


def theory_color(clave: str) -> str:
    """El color del algoritmo alejado del fondo: la teórica va punteada encima de su
    media simulada, y con el mismo color no se distinguiría. En el tema café se
    aclara; en el claro se oscurece, porque aclarada se perdería contra el papel."""
    canales = [int(curve_color(clave)[i:i + 2], 16) for i in (1, 3, 5)]
    claro = STATE.theme == "claro"
    return "#" + "".join(f"{c * 3 // 5 if claro else (c + 2 * 255) // 3:02x}" for c in canales)


def curve_visible(curva: str) -> bool:
    return curva not in STATE.hidden_curves


def chart_series() -> list[tuple[str, dict[str, Any]]]:
    """Los experimentos del último sweep que ya tienen puntos."""
    return [(clave, e) for clave, e in STATE.experiments.items() if e["puntos"]]


def on_curve_toggle(event) -> None:
    """Una casilla de «Mostrar en la gráfica». Delegado: las casillas se regeneran."""
    curva = event.target.getAttribute("data-curva") if event.target else None
    if not curva:
        return
    if event.target.checked:
        STATE.hidden_curves.discard(str(curva))
    else:
        STATE.hidden_curves.add(str(curva))
    render_chart()


def on_detail_change(event) -> None:
    STATE.detail_algorithm = str(event.target.value)
    render_results()
    render_downloads()
    if STATE.experiment and STATE.experiment["puntos"]:
        setup_trace()
    else:
        set_hidden("trace-panel", True)


def render_results() -> None:
    """Las casillas de «Mostrar en la gráfica» (que son también su leyenda) y la
    tabla de resultados del experimento en detalle."""

    def casilla(curva: str, muestra: str, texto: str) -> str:
        marcada = " checked" if curve_visible(curva) else ""
        return (f'<label class="legend-item"><input type="checkbox" data-curva="{html.escape(curva)}"{marcada} />'
                f"{muestra}{texto}</label>")

    leyenda = []
    for clave, e in chart_series():
        nombre = html.escape(e["nombre"])
        leyenda.append(casilla(f"{clave}:sim", f'<span class="legend-swatch" style="--c:{curve_color(clave)}"></span>',
                               f"{nombre}: media simulada"))
        if theory_for(e):
            leyenda.append(casilla(f"{clave}:teo",
                                   f'<span class="legend-swatch theory" style="--c:{theory_color(clave)}"></span>',
                                   f"{nombre}: teórica exacta"))
        else:
            leyenda.append(f'<span class="legend-item">{nombre}: sin curva teórica '
                           f'({html.escape(e.get("motivo_teorica") or "no disponible")}).</span>')
    if bound_curve():
        leyenda.append(casilla("cota", f'<span class="legend-swatch theory" style="--c:{TEMAS[STATE.theme]["tinta"]}"></span>',
                               "Cota inferior por validez"))
    if leyenda:
        leyenda.append(casilla("ic", '<span class="legend-swatch band"></span>', "IC 95 %"))
    marcado = "".join(leyenda)
    casillas = by_id("chart-toggles")
    if casillas.innerHTML != marcado:  # no regenerar en cada pasada: quita el foco
        casillas.innerHTML = marcado

    # Con un solo algoritmo no hay nada que elegir.
    set_hidden("detail-picker", len(STATE.experiments) < 2)
    detalle = by_id("detail-select")
    opciones = "".join(
        f'<option value="{html.escape(clave)}">{html.escape(e["nombre"])}</option>'
        for clave, e in STATE.experiments.items()
    )
    if detalle.innerHTML != opciones:
        detalle.innerHTML = opciones
    detalle.value = STATE.detail_algorithm or ""

    experiment = STATE.experiment
    puntos = experiment["puntos"] if experiment else []
    set_hidden("results-table", not puntos)
    if not puntos:
        by_id("results-table-body").innerHTML = ""
        return
    teorica = theory_for(experiment)
    filas = []
    for i, punto in enumerate(puntos):
        teo = f"<td>{teorica[i]:.4f}</td>" if teorica else '<td class="sin-dato">—</td>'
        filas.append(
            f"<tr><td>{float(punto['p']):.3f}</td><td>{float(punto['mean']):.4f}</td>"
            f"<td>{float(punto['ciLow']):.4f}</td><td>{float(punto['ciHigh']):.4f}</td>{teo}</tr>"
        )
    by_id("results-table-body").innerHTML = "".join(filas)
    set_text("results-table-summary", f"Tabla de resultados ({len(puntos)} valores de p)")


def render_chart() -> None:
    """Dibuja, por cada algoritmo del último sweep, su banda, su media y su
    teórica: las que estén palomeadas en «Mostrar en la gráfica»."""
    global CHART_MAX_Y
    set_hidden("chart-tooltip", True)
    left, right, top, bottom = CHART_LEFT, CHART_RIGHT, CHART_TOP, CHART_BOTTOM
    con_ic = curve_visible("ic")

    # (clave, puntos, teórica o None), solo con lo que está a la vista.
    curvas = []
    values: list[float] = []
    for clave, e in chart_series():
        puntos = e["puntos"] if curve_visible(f"{clave}:sim") else None
        teorica = theory_for(e) if curve_visible(f"{clave}:teo") else None
        if puntos:
            values += [float(p["ciHigh" if con_ic else "mean"]) for p in puntos] + [float(p["mean"]) for p in puntos]
        values += teorica or []
        curvas.append((clave, e["puntos"], puntos is not None, teorica))
    cota = bound_curve() if curve_visible("cota") else None
    values += cota[1] if cota else []
    max_y, divisions = nice_axis(max(values)) if values else (1.0, 4)
    CHART_MAX_Y = max_y
    render_chart_axis(max_y, divisions, top, bottom)

    def x_coord(p: float) -> float:
        return left + max(0.0, min(1.0, p)) * (right - left)

    def y_coord(value: float) -> float:
        return bottom - (value / max_y) * (bottom - top)

    def trazo(puntos: list[dict[str, float]], valores: list[float]) -> str:
        return " ".join(
            ("M" if i == 0 else "L") + f" {x_coord(float(pt['p'])):.2f},{y_coord(v):.2f}"
            for i, (pt, v) in enumerate(zip(puntos, valores))
        )

    # Bandas al fondo, medias encima y teóricas hasta arriba. El color va en
    # `style` y no en un atributo: el CSS de la clase pisaría el atributo.
    bandas, medias, teoricas = [], [], []
    for clave, puntos, simulada, teorica in curvas:
        if simulada:
            if con_ic:
                upper = [f"{x_coord(float(p['p'])):.2f},{y_coord(float(p['ciHigh'])):.2f}" for p in puntos]
                lower = [f"{x_coord(float(p['p'])):.2f},{y_coord(float(p['ciLow'])):.2f}" for p in reversed(puntos)]
                bandas.append(f'<path class="chart-confidence-band" style="fill:{curve_color(clave)}" '
                              f'd="M {" L ".join(upper + lower)} Z"></path>')
            medias.append(f'<path class="chart-mean-line" style="stroke:{curve_color(clave)}" '
                          f'd="{trazo(puntos, [float(p["mean"]) for p in puntos])}"></path>')
        if teorica:
            teoricas.append(f'<path class="chart-theory-line" style="stroke:{theory_color(clave)}" '
                            f'd="{trazo(puntos, teorica)}"></path>')
    if cota:
        teoricas.append(f'<path class="chart-theory-line chart-bound-line" style="stroke:{TEMAS[STATE.theme]["tinta"]}" '
                        f'd="{trazo(*cota)}"></path>')
    by_id("chart-curves").innerHTML = "".join(bandas + medias + teoricas)


def on_chart_pointer_move(event) -> None:
    series = chart_series()
    if not series:
        return
    svg = by_id("sweep-chart-svg")
    bounds = svg.getBoundingClientRect()
    if float(bounds.width) <= 0 or float(bounds.height) <= 0:
        return
    svg_x = (float(event.clientX) - float(bounds.left)) * CHART_WIDTH / float(bounds.width)
    p_value = max(0.0, min(1.0, (svg_x - CHART_LEFT) / (CHART_RIGHT - CHART_LEFT)))
    # Todos los algoritmos de un sweep comparten los valores de p.
    rejilla = series[0][1]["puntos"]
    i = min(range(len(rejilla)), key=lambda k: abs(float(rejilla[k]["p"]) - p_value))
    lineas = [f'<strong>p = {float(rejilla[i]["p"]):.3f}</strong>']
    for clave, e in series:
        if i >= len(e["puntos"]):
            continue
        point = e["puntos"][i]
        partes = []
        if curve_visible(f"{clave}:sim"):
            partes.append(f'{float(point["mean"]):.4f} [{float(point["ciLow"]):.4f}, {float(point["ciHigh"]):.4f}]')
        teorica = theory_for(e)
        if teorica and curve_visible(f"{clave}:teo"):
            partes.append(f"teórica {teorica[i]:.4f}")
        if partes:
            lineas.append(f'<span class="legend-swatch" style="--c:{curve_color(clave)}"></span> '
                          f'{html.escape(e["nombre"])}: ' + " · ".join(partes))
    cota = bound_curve()
    if cota and curve_visible("cota") and i < len(cota[1]):
        lineas.append(f"Cota inferior por validez: {cota[1][i]:.4f}")
    tooltip = by_id("chart-tooltip")
    tooltip.innerHTML = "<br>".join(lineas)
    chart_bounds = tooltip.parentElement.getBoundingClientRect()
    local_x = float(event.clientX) - float(chart_bounds.left)
    local_y = float(event.clientY) - float(chart_bounds.top)
    tooltip.hidden = False  # visible antes de medirlo: oculto mide 0 de ancho
    ancho = float(tooltip.offsetWidth or 176.0)
    alto = float(tooltip.offsetHeight or 68.0)
    tooltip.style.left = f"{max(4.0, min(local_x + 12.0, float(chart_bounds.width) - ancho - 4.0)):.1f}px"
    tooltip.style.top = f"{max(4.0, local_y - alto - 12.0):.1f}px"


#: Los temas, el predefinido primero. Los colores son los de :root en app.css,
#: repetidos aquí porque el SVG descargado no ve app.css.
TEMAS = {
    "cafe": {"nombre": "café", "fondo": "#15120f", "rejilla": "#352c25", "tinta": "#f1ebe3", "texto": "#b5a898"},
    "claro": {"nombre": "claro", "fondo": "#ffffff", "rejilla": "#d5dae3", "tinta": "#14213d", "texto": "#5b6577"},
}


def chart_svg_style() -> str:
    """El estilo de la gráfica descargada, en el tema a la vista. El color de cada
    curva ya va en su `style`."""
    t = TEMAS[STATE.theme]
    return (
        "<style>"
        f".chart-grid line{{stroke:{t['rejilla']}}}"
        f".chart-axis{{stroke:{t['tinta']};stroke-width:1}}"
        f".chart-axis-labels text{{fill:{t['texto']};font:13px 'STIX Two Text',Georgia,'Times New Roman',serif}}"
        f".chart-axis-labels .chart-axis-title{{fill:{t['tinta']};font-size:14px}}"
        ".chart-confidence-band{fill-opacity:.18;stroke:none}"
        ".chart-mean-line{fill:none;stroke-width:2.5}"
        ".chart-theory-line{fill:none;stroke-width:1.5;stroke-dasharray:6 5}"
        "</style>"
        f'<rect width="100%" height="100%" fill="{t["fondo"]}"/>'
    )


def apply_theme() -> None:
    by_id("root").setAttribute("data-theme", STATE.theme)
    otro = next(t for t in TEMAS if t != STATE.theme)
    set_text("theme-btn", f"Usar tema {TEMAS[otro]['nombre']}")


def toggle_theme(*_args) -> None:
    STATE.theme = next(t for t in TEMAS if t != STATE.theme)
    try:
        window.localStorage.setItem("tema", STATE.theme)
    except Exception:  # sin almacenamiento (modo privado): el tema dura la sesión
        pass
    apply_theme()
    # Nodos, minimapa y teóricas llevan colores calculados: se redibujan.
    render_all()
    render_chart()
    render_results()


def experiment_basename(experiment: dict[str, Any], algoritmos: str = "") -> str:
    red = re.sub(r"[^A-Za-z0-9_-]+", "-", experiment["red"]).strip("-") or "red"
    params = experiment["params"]
    return f"sweep_{red}_{algoritmos or params['algorithm']}_semilla{params['semilla']}"


def experiment_csv(experiment: dict[str, Any]) -> str:
    """La curva, una fila por p. `repr` conserva todos los dígitos del float."""
    n = experiment["simulaciones_por_p"]
    teorica = theory_for(experiment)
    filas = ["p,media,ic_bajo,ic_alto,simulaciones,teorica"]
    for i, punto in enumerate(experiment["puntos"]):
        filas.append(
            f"{float(punto['p'])!r},{float(punto['mean'])!r},"
            f"{float(punto['ciLow'])!r},{float(punto['ciHigh'])!r},{n},"
            + (repr(teorica[i]) if teorica else "")
        )
    return "\n".join(filas) + "\n"


def experiment_json(experiment: dict[str, Any]) -> str:
    """Todo lo necesario para repetir el experimento, más su resultado.

    `red` va en el formato de importación del editor, así que se puede volver a
    cargar tal cual.
    """
    params = experiment["params"]
    teorica = theory_for(experiment)
    documento = {
        "fecha": experiment["fecha"],
        "red": params["graph"],
        "nombre_red": experiment["red"],
        "algoritmo": {
            "clave": params["algorithm"],
            "nombre": experiment["nombre"],
            "parametros": params["algorithmParams"],
            "codigo": params["fuente"],
        },
        "modo": params["modo"],
        "experimento": {
            "puntos_p": params["numPPoints"],
            "simulaciones_por_p": params["totalSimulationsPerP"],
            "iteraciones": params["iterations"],
            "rondas": params["rounds"],
            "semilla": params["semilla"],
        },
        "resultado": {
            "completo": experiment["completo"],
            "simulaciones_por_p": experiment["simulaciones_por_p"],
            "motivo_sin_teorica": None if teorica else experiment.get("motivo_teorica"),
            "puntos": [
                {
                    "p": float(punto["p"]),
                    "media": float(punto["mean"]),
                    "ic_bajo": float(punto["ciLow"]),
                    "ic_alto": float(punto["ciHigh"]),
                    "teorica": teorica[i] if teorica else None,
                }
                for i, punto in enumerate(experiment["puntos"])
            ],
        },
    }
    return json.dumps(documento, indent=2, ensure_ascii=False)


def chart_svg(markup: str) -> str:
    """El SVG de la gráfica, autónomo: con su namespace, su estilo y su fondo."""
    if "xmlns=" not in markup.split(">", 1)[0]:
        markup = markup.replace("<svg", '<svg xmlns="http://www.w3.org/2000/svg"', 1)
    if " width=" not in markup.split(">", 1)[0]:
        # Sin tamaño propio, otros programas (Inkscape, LaTeX) lo abren diminuto.
        markup = markup.replace("<svg", f'<svg width="{CHART_WIDTH:g}" height="440"', 1)
    apertura, resto = markup.split(">", 1)
    return f'<?xml version="1.0" encoding="UTF-8"?>\n{apertura}>{chart_svg_style()}{resto}\n'


def render_downloads() -> None:
    vacio = STATE.experiment is None or not STATE.experiment["puntos"]
    for element_id in ("download-csv-btn", "download-json-btn", "download-svg-btn"):
        by_id(element_id).disabled = vacio


def download_experiment(formato: str) -> None:
    experiment = STATE.experiment
    if experiment is None or not experiment["puntos"]:
        return
    nombre = experiment_basename(experiment)
    if formato == "csv":
        download(f"{nombre}.csv", experiment_csv(experiment), "text/csv")
    elif formato == "json":
        download(f"{nombre}.json", experiment_json(experiment), "application/json")
    else:
        # La gráfica trae todas las curvas, no solo la del experimento en detalle.
        nombre = experiment_basename(experiment, "-".join(clave for clave, _ in chart_series()))
        download(f"{nombre}.svg", chart_svg(str(by_id("sweep-chart-svg").outerHTML)), "image/svg+xml")


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
    by_id("sweep-run-btn").disabled = not bool(tab.nodes) or STATE.sweep_running or not STATE.sweep_algorithms
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
    # Las secciones .only-analysis / .only-editor / .only-code se esconden por CSS
    # según esta clase.
    for vista in ("analysis", "editor", "code"):
        by_id("app").classList.toggle(f"view-{vista}", STATE.active_view == vista)
    for vista in ("analysis", "code"):
        by_id(f"view-{vista}").classList.toggle("active", STATE.active_view == vista)
        by_id(f"view-{vista}").setAttribute("aria-selected", str(STATE.active_view == vista).lower())
    render_code_view()
    set_text("message-mode-help", MODOS_AYUDA.get(STATE.message_mode, ""))
    render_sweep_plan()
    if tab.nodes:
        valores = [float(node.value) for node in tab.nodes]
        muestra = ", ".join(number_label(v) for v in valores[:12]) + (", …" if len(valores) > 12 else "")
        set_text(
            "network-values",
            f"Valores: {muestra}. Discrepancia inicial: {number_label(max(valores) - min(valores))}.",
        )
    else:
        set_text("network-values", "Sin nodos: entra a «Editar red» para armar una.")
    set_text(
        "network-summary",
        f"{tab.name}: {len(tab.nodes)} nodo{'' if len(tab.nodes) == 1 else 's'} · "
        f"{len(tab.edges)} arista{'' if len(tab.edges) == 1 else 's'} · "
        + ("dirigida" if tab.directed else "no dirigida"),
    )


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
    # Fuera del editor el teclado no toca el grafo: Supr borraría un nodo que no se ve.
    editing = tag in {"input", "select", "textarea"} or STATE.active_view != "editor"
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


def trace_status(texto: str, *, error: bool = False) -> None:
    caja = by_id("trace-status")
    caja.textContent = texto
    caja.classList.toggle("sweep-error", error)


def setup_trace() -> None:
    """Prepara los deslizadores para el sweep recién empezado y pide la primera traza."""
    global TRACE
    experiment = STATE.experiment
    puntos = experiment["puntos"]
    TRACE = None
    deslizador_p = by_id("trace-p")
    deslizador_p.max = str(len(puntos) - 1)
    # Empieza en el p más cercano a 0.5: en los extremos casi nunca pasa nada.
    deslizador_p.value = str(min(range(len(puntos)), key=lambda i: abs(float(puntos[i]["p"]) - 0.5)))
    by_id("trace-sim").max = str(experiment["simulaciones_por_p"])
    by_id("trace-sim").value = "1"
    set_text("trace-sim-out", f"de {miles(experiment['simulaciones_por_p'])}")
    rondas = int(experiment["params"]["rounds"])
    by_id("trace-round").max = str(rondas)
    by_id("trace-round").value = "1"
    set_hidden("trace-panel", False)
    request_trace()


def request_trace(*_args) -> None:
    """Pide al Worker del editor que repita la simulación del sweep que marcan los
    deslizadores de p y simulación."""
    global TRACE_PEDIDO
    experiment = STATE.experiment
    if experiment is None or not experiment["puntos"] or ENGINE is None:
        return
    indice_p = int(by_id("trace-p").value)
    simulacion = trace_sim_field()
    if simulacion is None:
        return  # a medio escribir (vacío o no es número): se espera al siguiente tecleo
    p = float(experiment["puntos"][indice_p]["p"])
    set_text("trace-p-out", f"{p:.3f}")
    TRACE_PEDIDO += 1
    trace_status("Repitiendo la simulación…")
    # Todo lo del sweep (para rehacer su reparto y sus semillas) más cuál simulación.
    ENGINE.postMessage(to_js({"type": "traza", "id": TRACE_PEDIDO, "params": {
        **experiment["params"],
        "indiceP": indice_p,
        "simulacion": simulacion - 1,
    }}, dict_converter=Object.fromEntries))


def trace_sim_field() -> int | None:
    """El número de simulación escrito, acotado a las que corrió el sweep.

    ``None`` si no se puede leer como número: mientras se escribe, el campo pasa
    por estados vacíos, y eso no debe pedir nada.
    """
    try:
        escrito = int(float(str(by_id("trace-sim").value)))
    except (ValueError, OverflowError):
        return None
    return max(1, min(int(STATE.experiment["simulaciones_por_p"]), escrito))


def normalize_trace_sim(*_args) -> None:
    """Al salir del campo, deja escrito el número que se está mostrando."""
    if STATE.experiment is None:
        return
    simulacion = trace_sim_field()
    by_id("trace-sim").value = str(simulacion or 1)
    if simulacion is None:
        request_trace()


def receive_trace(message: dict[str, Any]) -> None:
    global TRACE
    if int(message.get("id") or 0) != TRACE_PEDIDO:
        return  # llegó tarde: el deslizador ya pidió otra
    TRACE = json.loads(str(message.get("resultado") or "{}"))
    render_trace()


def on_trace_chip(event) -> None:
    chip = event.target.closest("[data-ronda]")
    if chip:
        by_id("trace-round").value = str(chip.getAttribute("data-ronda"))
        render_trace()


def render_trace(*_args) -> None:
    """La ronda elegida de la traza: discrepancias, resumen y una fila por nodo."""
    if TRACE is None or STATE.experiment is None:
        return
    valores = TRACE["valores"]
    rondas = len(valores) - 1
    r = max(0, min(rondas, int(by_id("trace-round").value)))
    set_text("trace-round-out", f"{r} de {rondas}")
    discrepancias = TRACE["discrepancias"]
    # Con muchas rondas, una ventana alrededor de la actual: mil botones no se leen.
    inicio = max(0, min(r - 7, rondas - 14))
    ventana = range(inicio, min(rondas, inicio + 14) + 1)
    chips = ["… " if inicio > 0 else ""]
    for k in ventana:
        activo = " active" if k == r else ""
        chips.append(
            f'<button class="trace-chip{activo}" type="button" data-ronda="{k}" '
            f'aria-label="Ronda {k}: discrepancia {number_label(discrepancias[k])}">{discrepancias[k]:.3g}</button>'
        )
    if ventana[-1] < rondas:
        chips.append(" …")
    by_id("trace-chips").innerHTML = "".join(chips)

    nombres = [str(node["id"]) for node in STATE.experiment["params"]["graph"]["nodes"]]
    filas = []
    cambiaron = 0
    for i, nombre in enumerate(nombres):
        despues = valores[r][i]
        if r == 0:
            filas.append(f"<tr><td>{html.escape(nombre)}</td><td>—</td><td>{number_label(despues)}</td><td>—</td><td></td></tr>")
            continue
        antes = valores[r - 1][i]
        cambio = not math.isclose(antes, despues, abs_tol=1e-12)
        cambiaron += cambio
        ronda = TRACE["rondas"][r - 1]
        eligio = ronda["eligio"][i]
        recibidos = ", ".join(
            f'<span class="copio" title="copió su valor">{html.escape(nombres[j])}</span>' if j == eligio
            else html.escape(nombres[j])
            for j in ronda["recibio"][i]
        ) or "—"
        clase = ' class="cambio"' if cambio else ""
        filas.append(
            f"<tr{clase}><td>{html.escape(nombre)}</td><td>{number_label(antes)}</td>"
            f"<td>{number_label(despues)}</td><td>{recibidos}</td><td>{'●' if cambio else ''}</td></tr>"
        )
    by_id("trace-body").innerHTML = "".join(filas)
    if r == 0:
        trace_status(f"Ronda 0: valores iniciales. Discrepancia {number_label(discrepancias[0])}.")
    else:
        trace_status(
            f"Ronda {r}: cambiaron {cambiaron} de {len(nombres)} nodos. "
            f"Discrepancia {number_label(discrepancias[r - 1])} → {number_label(discrepancias[r])}. "
            f"(Corrió en la pasada {int(TRACE['iteracion']) + 1}.)"
        )


def download(filename: str, content: str, mime: str) -> None:
    blob = Blob.new(
        to_js([content]),
        to_js({"type": mime}, dict_converter=Object.fromEntries),
    )
    url = URL.createObjectURL(blob)
    link = document.createElement("a")
    link.href = url
    link.download = filename
    link.hidden = True
    document.body.appendChild(link)
    link.click()

    def cleanup():
        URL.revokeObjectURL(url)
        link.remove()

    window.setTimeout(retain(cleanup), 750)


def export_json(*_args) -> None:
    download("graph.json", json.dumps(current_graph_json(), indent=2, ensure_ascii=False), "application/json")


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


def open_topology_dialog(*_args) -> None:
    select_topology(None)
    set_hidden("topology-dialog", False)
    by_id("topology-cancel").focus()


def close_topology_dialog(*_args) -> None:
    set_hidden("topology-dialog", True)


def select_topology(clave: str | None) -> None:
    """Pasa de la lista de botones (``None``) a los parámetros de una topología.

    Botones y campos salen de ``TOPOLOGIAS``. Los campos se generan aquí, una vez
    por topología, y no en cada tecla: regenerarlos le quitaría el foco al que se
    está escribiendo.
    """
    import topologias  # aquí y no arriba: importar networkx tarda y no siempre se usa

    TOPOLOGY["clave"], TOPOLOGY["valores"] = clave, {}
    set_hidden("topology-list", clave is not None)
    for element_id in ("topology-detail", "topology-back", "topology-accept"):
        set_hidden(element_id, clave is None)
    if clave is None:
        set_text("topology-title", "Topologías")
        by_id("topology-list").innerHTML = "".join(
            f'<button class="quiet-btn" type="button" data-topologia="{html.escape(otra)}">'
            f'{html.escape(entrada["nombre"])}</button>'
            for otra, entrada in topologias.TOPOLOGIAS.items()
        )
        return
    entrada = topologias.TOPOLOGIAS[clave]
    set_text("topology-title", entrada["nombre"])
    set_text("topology-description", entrada["descripcion"])
    by_id("topology-params").innerHTML = "".join(
        f'<label class="field-label" for="topology-param-{nombre}">{html.escape(etiqueta)}</label>'
        f'<input id="topology-param-{nombre}" class="agreed-input" type="number" data-param="{nombre}" '
        f'value="{omision}" min="{minimo}" max="{maximo}" step="{1 if isinstance(omision, int) else 0.05}" />'
        for nombre, (etiqueta, omision, minimo, maximo) in entrada["parametros"].items()
    )
    render_topology_preview()
    primero = by_id(f"topology-param-{next(iter(entrada['parametros']))}")
    primero.focus()
    primero.select()


def topology_svg(datos: dict[str, Any]) -> str:
    """La imagen de referencia: la misma gráfica que se va a cargar, en chico."""
    centros = {nodo["id"]: (nodo["position"]["x"], nodo["position"]["y"]) for nodo in datos["nodes"]}
    xs, ys = zip(*centros.values())
    ancho, alto = max(xs) - min(xs), max(ys) - min(ys)
    # En una gráfica grande el nodo a escala no se vería: se le da un mínimo.
    radio = max(30.0, max(ancho, alto) * 0.012)
    margen = radio + 20
    lineas = "".join(
        '<line x1="{:.1f}" y1="{:.1f}" x2="{:.1f}" y2="{:.1f}"></line>'.format(
            *centros[arista["source"]], *centros[arista["target"]]
        )
        for arista in datos["edges"]
    )
    circulos = "".join(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{radio:.1f}"></circle>' for x, y in centros.values())
    return (
        f'<svg viewBox="{min(xs) - margen:.1f} {min(ys) - margen:.1f} {ancho + 2 * margen:.1f} '
        f'{alto + 2 * margen:.1f}" role="img" aria-label="Vista previa de la topología">{lineas}{circulos}</svg>'
    )


def render_topology_preview() -> dict[str, Any] | None:
    """Dibuja la topología elegida con lo escrito en los campos, o dice por qué no se puede.

    Devuelve el JSON de la gráfica, que es justo lo que «Cargar» importa.
    """
    import topologias

    try:
        datos, mensaje = topologias.generar(TOPOLOGY["clave"], TOPOLOGY["valores"]), ""
    except Exception as error:
        datos, mensaje = None, str(error)
    set_text("topology-error", mensaje)
    set_hidden("topology-error", datos is not None)
    by_id("topology-accept").disabled = datos is None
    by_id("topology-preview").innerHTML = topology_svg(datos) if datos else ""
    set_text("topology-count", f'{len(datos["nodes"])} nodos · {len(datos["edges"])} aristas' if datos else "")
    return datos


def accept_topology(*_args) -> None:
    import topologias

    datos = render_topology_preview()
    if datos is None:
        return
    clave = TOPOLOGY["clave"]
    valores = topologias.leer_parametros(clave, TOPOLOGY["valores"]).values()
    close_topology_dialog()
    # Entra igual que un archivo importado: pestaña nueva con ese nombre.
    finish_import(json.dumps(datos), f'{topologias.TOPOLOGIAS[clave]["nombre"]} ({", ".join(map(str, valores))})')
    fit_view()


def on_topology_list_click(event) -> None:
    boton = event.target.closest("[data-topologia]")
    if boton:
        select_topology(str(boton.getAttribute("data-topologia")))


def on_topology_input(event) -> None:
    """Delegado en el contenedor, como `on_param_change`: los campos se regeneran."""
    nombre = event.target.getAttribute("data-param") if event.target else None
    if nombre:
        TOPOLOGY["valores"][str(nombre)] = str(event.target.value)
        render_topology_preview()


def on_topology_keydown(event) -> None:
    if event.key == "Escape":
        close_topology_dialog()
    elif event.key == "Enter" and str(event.target.tagName).lower() == "input":
        event.preventDefault()
        accept_topology()


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
    set_text("message-mode-help", MODOS_AYUDA.get(STATE.message_mode, ""))


def on_param_change(event) -> None:
    """Guarda el valor de un campo de parámetro en su algoritmo (`data-clave`).

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
    clave = str(event.target.getAttribute("data-clave") or STATE.algorithm)
    algorithm_params(clave)  # asegura que el diccionario del algoritmo exista
    STATE.algorithm_params[clave][str(name)] = valor


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
        elif kind == "traza":
            receive_trace(message)
        elif kind == "error" and message.get("para") == "traza":
            if int(message.get("id") or 0) == TRACE_PEDIDO:
                trace_status(f"Error: {message.get('message')}", error=True)
                if message.get("traza"):
                    report_engine_error(message)
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
    if view == "editor":
        # Mientras el lienzo está oculto mide 0 × 0: se encuadra al volver a verse.
        fit_view()


def sweep_field(element_id: str) -> int:
    """El valor del campo llevado a sus límites. Vacío o ilegible cuenta como el mínimo."""
    low, high = SWEEP_LIMITS[element_id]
    try:
        return min(high, max(low, int(float(str(by_id(element_id).value)))))
    except (ValueError, OverflowError):
        return low


def miles(n: int) -> str:
    return f"{n:,}".replace(",", " ")


def seed_field() -> int | None:
    """La semilla escrita, o ``None`` si el campo está vacío.

    Eleva ``ValueError`` si no es un entero entre 0 y ``SEED_MAX``: sortear otra
    en silencio haría creer que el experimento se puede repetir.
    """
    texto = str(by_id("seed-input").value).strip()
    if not texto:
        return None
    if not (texto.isascii() and texto.isdigit()) or int(texto) > SEED_MAX:
        raise ValueError(f"La semilla debe ser un entero entre 0 y {miles(SEED_MAX)}.")
    return int(texto)


def sweep_plan() -> dict[str, int]:
    """Lo que correrá el sweep con los campos actuales.

    Repite el reparto de `iter_sweep` (simulaciones por pasada y recorte de
    pasadas); smoke_ui comprueba que los dos coincidan.
    """
    pedidas = sweep_field("simulations-input")
    por_pasada = max(1, pedidas // sweep_field("iterations-input"))
    pasadas = min(sweep_field("iterations-input"), pedidas // por_pasada)
    return {
        "puntos": sweep_field("p-points-input"),
        "pedidas": pedidas,
        "por_pasada": por_pasada,
        "pasadas": pasadas,
        "rondas": sweep_field("rounds-input"),
    }


def render_sweep_plan(*_args) -> None:
    plan = sweep_plan()
    por_p = plan["por_pasada"] * plan["pasadas"]

    rondas = "1 ronda" if plan["rondas"] == 1 else f"{plan['rondas']} rondas"
    texto = (
        f"{plan['puntos']} valores de p × {miles(por_p)} simulaciones = "
        f"{miles(plan['puntos'] * por_p)} simulaciones de {rondas}, "
        f"en {plan['pasadas']} pasadas de {miles(plan['por_pasada'])} por p."
    )
    if por_p != plan["pedidas"]:
        texto += f" Pediste {miles(plan['pedidas'])}: en pasadas iguales caben {miles(por_p)}."
    if len(STATE.sweep_algorithms) > 1:
        texto += f" Eso, por cada uno de los {len(STATE.sweep_algorithms)} algoritmos."
    elif not STATE.sweep_algorithms:
        texto = "Palomea al menos un algoritmo."
    try:
        semilla = seed_field()
        texto += " Semilla: al azar." if semilla is None else f" Semilla: {semilla}."
    except ValueError as error:
        texto += f" {error}"
    set_text("sweep-plan", texto)


def set_sweep_inputs_disabled(disabled: bool) -> None:
    for element_id in ("p-points-input", "simulations-input", "iterations-input", "rounds-input", "seed-input"):
        by_id(element_id).disabled = disabled
    set_hidden("sweep-run-btn", disabled)
    set_hidden("sweep-stop-btn", not disabled)


def stop_sweep(*_args) -> None:
    finish_sweep()
    by_id("sweep-status").innerHTML = "<p>Ajusta los parámetros y ejecuta el sweep.</p>"


def render_sweep_status() -> None:
    """Una línea por algoritmo del sweep: su avance o su error."""
    lineas = []
    for experiment in STATE.experiments.values():
        clase = ' class="sweep-error"' if experiment["error"] else ""
        lineas.append(f'<p{clase}>{html.escape(experiment["nombre"])}: {html.escape(experiment["estado"])}</p>')
    by_id("sweep-status").innerHTML = "".join(lineas)


def start_sweep(*_args) -> None:
    if not STATE.tab.nodes or STATE.sweep_running or not STATE.sweep_algorithms:
        return
    try:
        semilla = seed_field()
    except ValueError as error:
        by_id("sweep-status").innerHTML = f'<p class="sweep-error">{html.escape(str(error))}</p>'
        return
    # Siempre viaja una semilla: si no la escribieron se sortea aquí, para poder
    # mostrarla y que cualquier corrida se pueda repetir.
    STATE.sweep_seed = random.randrange(SEED_MAX + 1) if semilla is None else semilla
    comunes = {
        "graph": current_graph_json(),
        "modo": STATE.message_mode,
        "numPPoints": sweep_field("p-points-input"),
        "totalSimulationsPerP": sweep_field("simulations-input"),
        "iterations": sweep_field("iterations-input"),
        "rounds": sweep_field("rounds-input"),
        # La misma para todos: como iter_sweep siembra cada bloque (pasada, p),
        # la curva de un algoritmo no depende de cuáles otros se palomearon.
        "semilla": STATE.sweep_seed,
    }
    fecha = datetime.now(timezone.utc).isoformat(timespec="seconds")
    # Un experimento por algoritmo, cada uno con la forma de un sweep de uno solo:
    # así la tabla, el desglose y las descargas no saben que hay varios.
    STATE.experiments = {
        clave: {
            "params": {
                **comunes,
                "algorithm": clave,
                # Nombre explícito: este dict ya se llama `params` a nivel del mensaje,
                # y esta clave son los parámetros del algoritmo, no los del sweep.
                "algorithmParams": algorithm_params(clave),
                "fuente": user_source(clave),
            },
            "red": STATE.tab.name,
            "nombre": entrada_de(clave)["nombre"],
            "fecha": fecha,
            "puntos": [],
            "simulaciones_por_p": 0,
            "completo": False,
            # La manda el Worker antes de la primera iteración (teoria.curva_exacta).
            "teorica": None,
            "motivo_teorica": "calculando…",
            "cota": None,
            "estado": "cargando motor Python del sweep…",
            "error": False,
        }
        for clave in STATE.sweep_algorithms
    }
    STATE.detail_algorithm = STATE.sweep_algorithms[0]
    STATE.sweep_running = True
    set_sweep_inputs_disabled(True)
    set_hidden("trace-panel", True)
    render_chart()
    render_results()
    render_downloads()
    render_sweep_status()
    for clave in STATE.experiments:
        start_sweep_worker(clave)


def start_sweep_worker(clave: str) -> None:
    """El Worker que corre el sweep de un algoritmo y va llenando su experimento."""
    experiment = STATE.experiments[clave]
    worker = Worker.new(
        "./js/sweep-worker.js",
        to_js({"type": "module"}, dict_converter=Object.fromEntries),
    )
    WORKERS[clave] = worker

    def fallo(texto: str) -> None:
        experiment.update(estado=f"Error: {texto}", error=True)
        render_sweep_status()
        finish_sweep(clave)

    def on_message(event):
        data = event.data.to_py() if hasattr(event.data, "to_py") else event.data
        message = dict(data)
        kind = str(message.get("type", ""))
        if kind == "iteration":
            primera = not experiment["puntos"]
            experiment.update(
                puntos=[dict(point) for point in message.get("points", [])],
                simulaciones_por_p=int(message["simulationsPerP"]),
                completo=bool(message.get("done")),
            )
            render_chart()
            render_results()
            render_downloads()
            if experiment is STATE.experiment:
                if primera:
                    setup_trace()
                else:
                    # Cada pasada suma simulaciones que ya se pueden inspeccionar.
                    total = experiment["simulaciones_por_p"]
                    by_id("trace-sim").max = str(total)
                    set_text("trace-sim-out", f"de {miles(total)}")
            completed = " — completo" if message.get("done") else ""
            experiment["estado"] = (
                f'Iteración {int(message["iteration"])+1}/{int(message["totalIterations"])} — '
                f'{int(message["simulationsPerP"])} sims/p{completed} · semilla {STATE.sweep_seed}'
            )
            render_sweep_status()
        elif kind == "teorica":
            valores = message.get("valores")
            experiment["teorica"] = [float(v) for v in valores] if valores else None
            experiment["motivo_teorica"] = str(message.get("motivo") or "")
            cota = message.get("cota")
            experiment["cota"] = [float(v) for v in cota] if cota else None
            render_chart()
            render_results()
        elif kind == "end":
            finish_sweep(clave)
        elif kind == "error":
            # El traceback del código pegado se queda en el panel del editor,
            # que es donde está la caja de texto que hay que corregir.
            if message.get("traza"):
                report_engine_error(message)
            fallo(str(message.get("message")))

    worker.addEventListener("message", retain(on_message))
    worker.addEventListener("error", retain(lambda event: fallo(str(event.message))))
    worker.postMessage(to_js({"type": "run", "params": experiment["params"]}, dict_converter=Object.fromEntries))


def finish_sweep(clave: str | None = None) -> None:
    """Termina el Worker de `clave` (o todos). El sweep acaba cuando no queda ninguno:
    el error de un algoritmo no detiene a los demás."""
    for k in [clave] if clave else list(WORKERS):
        worker = WORKERS.pop(k, None)
        if worker is not None:
            worker.terminate()
    if not WORKERS:
        STATE.sweep_running = False
        set_sweep_inputs_disabled(False)
        render_controls()


def open_user_code(*_args) -> None:
    STATE.algorithm = CLAVE_USUARIO  # «Código» muestra el algoritmo del selector
    change_view("code")


def bind_events() -> None:
    canvas = by_id("graph-canvas")
    listen(by_id("graph-tabs"), "click", on_tabs_click)
    listen(by_id("theme-btn"), "click", toggle_theme)
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
    listen(by_id("topology-btn"), "click", open_topology_dialog)
    listen(by_id("topology-list"), "click", on_topology_list_click)
    listen(by_id("topology-params"), "input", on_topology_input)
    listen(by_id("topology-dialog"), "keydown", on_topology_keydown)
    listen(by_id("topology-back"), "click", lambda _event: select_topology(None))
    listen(by_id("topology-cancel"), "click", close_topology_dialog)
    listen(by_id("topology-accept"), "click", accept_topology)
    listen(by_id("export-btn"), "click", export_json)
    listen(by_id("clear-btn"), "click", clear_graph)
    listen(by_id("algorithm-select"), "change", on_algorithm_change)
    listen(by_id("algorithm-checks"), "change", on_algorithm_check)
    listen(by_id("chart-toggles"), "change", on_curve_toggle)
    listen(by_id("detail-select"), "change", on_detail_change)
    listen(by_id("message-mode-select"), "change", on_message_mode_change)
    listen(by_id("algorithm-params"), "change", on_param_change)
    listen(by_id("algorithm-params"), "input", on_param_change)
    listen(by_id("user-code"), "input", on_user_code_change)
    listen(by_id("simulate-btn"), "click", simulate)
    listen(by_id("toggle-simulation-btn"), "click", toggle_simulation)
    listen(by_id("reset-simulation-btn"), "click", reset_simulation)
    listen(by_id("view-analysis"), "click", lambda _event: change_view("analysis"))
    listen(by_id("view-code"), "click", lambda _event: change_view("code"))
    listen(by_id("open-code-btn"), "click", open_user_code)
    listen(by_id("copy-code-btn"), "click", copy_code)
    listen(by_id("edit-copy-btn"), "click", edit_copy)
    listen(by_id("edit-network-btn"), "click", lambda _event: change_view("editor"))
    listen(by_id("back-to-analysis-btn"), "click", lambda _event: change_view("analysis"))
    listen(by_id("zoom-in-btn"), "click", lambda _event: zoom_by(1.2))
    listen(by_id("zoom-out-btn"), "click", lambda _event: zoom_by(1 / 1.2))
    listen(by_id("fit-view-btn"), "click", fit_view)
    listen(by_id("sweep-controls"), "input", render_sweep_plan)
    listen(by_id("sweep-run-btn"), "click", start_sweep)
    listen(by_id("sweep-stop-btn"), "click", stop_sweep)
    listen(by_id("trace-p"), "input", request_trace)
    listen(by_id("trace-sim"), "input", request_trace)
    listen(by_id("trace-sim"), "change", normalize_trace_sim)
    listen(by_id("trace-round"), "input", render_trace)
    listen(by_id("trace-chips"), "click", on_trace_chip)
    listen(by_id("download-csv-btn"), "click", lambda _event: download_experiment("csv"))
    listen(by_id("download-json-btn"), "click", lambda _event: download_experiment("json"))
    listen(by_id("download-svg-btn"), "click", lambda _event: download_experiment("svg"))
    listen(by_id("sweep-chart-svg"), "pointermove", on_chart_pointer_move)
    listen(by_id("sweep-chart-svg"), "pointerleave", hide_chart_tooltip)
    listen(by_id("app-dialog-cancel"), "click", close_dialog)
    listen(by_id("app-dialog-accept"), "click", submit_dialog)
    listen(by_id("app-dialog-input"), "keydown", on_dialog_keydown)


def main() -> None:
    global PLANTILLA_USUARIO, COPY_RESET
    PLANTILLA_USUARIO = str(by_id("user-code").value)
    COPY_RESET = retain(lambda: set_text("copy-code-btn", "Copiar"))
    # bootstrap.js ya puso el tema guardado en <html>; aquí solo se valida.
    guardado = by_id("root").getAttribute("data-theme")
    STATE.theme = guardado if guardado in TEMAS else next(iter(TEMAS))
    apply_theme()
    bind_events()
    start_engine_worker()
    render_all()
    render_chart()
    render_downloads()
    window.graphSimulatorReady = True


if __name__ == "__main__":
    main()
