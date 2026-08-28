← [Índice](../DOCUMENTACION.md) · Anterior: [04 · Barrido (sweep)](04-barrido-sweep.md)

# 05 · Controlador (`python/app.py`)

1103 líneas. El archivo grande y el **único** que importa `js` y `pyodide.ffi`.
Hace de controlador completo: mantiene el estado, escucha el DOM, regenera el SVG
y coordina el Worker del sweep.

```python
from js import Blob, Object, URL, Worker, document, window
from pyodide.ffi import create_proxy, to_js
```

---

## 1. Estado global del módulo

```python
STATE = AppState()              # todo el estado de la aplicación
PROXIES: list[Any] = []         # proxies retenidos (ver retain)
WORKER = None                   # Worker del sweep, si está corriendo
DRAG: dict = {}                 # arrastre de nodo en curso
CONNECTION: dict = {}           # conexión Shift en curso
PAN: dict = {}                  # desplazamiento con Espacio en curso
LAST_NODE_CLICK: dict = {}      # doble clic manual
DIALOG_CALLBACK = None          # callback del diálogo abierto
CHART_POINTS: list = []         # última curva del sweep (para el tooltip)
CHART_MAX_Y = 1.0               # escala Y actual de la gráfica
```

`DRAG`, `CONNECTION` y `PAN` son **mutuamente excluyentes**: son los tres modos
de la máquina de estados del puntero (sección 6). Se usan `dict` mutables en vez
de variables globales para poder hacer `.clear()`/`.update()` sin declarar
`global` en cada handler.

---

## 2. Utilidades de DOM y proxies

| Función | Qué hace |
|---|---|
| `by_id(element_id)` | `document.getElementById`. |
| `set_hidden(id, hidden)` | Escribe la propiedad `hidden` si el elemento existe. |
| `set_text(id, value)` | Escribe `textContent`. |
| `listen(target, event_name, callback)` | `addEventListener` **pasando por `retain()`**. |
| `retain(callback)` | Envuelve en `create_proxy` y guarda en `PROXIES`. |

### `retain()` es obligatorio

```python
def retain(callback):
    proxy = create_proxy(callback)
    PROXIES.append(proxy)
    return proxy
```

Pyodide crea un proxy para que JavaScript pueda invocar una función de Python.
Si Python deja de referenciar ese proxy, el recolector de basura lo destruye —
pero JavaScript **sigue teniéndolo registrado como listener**. El resultado es un
evento que deja de responder, sin error ni traza. La lista `PROXIES` mantiene una
referencia viva durante toda la sesión.

Aplica a todo lo que cruce la frontera, no solo a los listeners: también al
callback de `window.setTimeout` en `export_json()` y al `.then()` de
`file.text()` en `on_file_change()`.

---

## 3. El diálogo modal

Reemplaza a `prompt()`/`confirm()` para funcionar donde el navegador los bloquea.

```python
open_dialog(title, message, callback=None, *, initial="",
            input_mode=True, accept_label="Aceptar", show_cancel=True)
```

| Función | Qué hace |
|---|---|
| `open_dialog(...)` | Guarda `callback` en `DIALOG_CALLBACK`, rellena textos, muestra u oculta el input, y enfoca (input o botón Aceptar). |
| `close_dialog()` | Limpia el callback y oculta. |
| `submit_dialog()` | Llama al callback con el texto del input. **Cierra solo si devuelve `True`.** |
| `dialog_error(message)` | Pone el mensaje en rojo (clase `error`) y devuelve el foco al input, sin cerrar. |
| `on_dialog_keydown(event)` | `Enter` → enviar, `Escape` → cerrar. |
| `notify(message)` | Intenta `window.alert`; si lanza, cae al diálogo en modo solo-lectura. |

### El patrón de validación

El valor de retorno del callback controla el cierre. Así se valida sin cerrar y
reabrir:

```python
def commit(value: str) -> bool:
    try:
        parsed = float(value)
    except ValueError:
        parsed = -1
    if not (0 < parsed <= 1):
        dialog_error("El valor debe ser un número en el intervalo (0, 1]")
        return False          # ← se queda abierto, con el error a la vista
    edge.weight = value       # se guarda el texto original, no el float
    mark_graph_changed()
    render_all()
    return True               # ← cierra
```

---

## 4. Render

Siete funciones. Ninguna hace diffing: **reconstruyen `innerHTML` desde `STATE`**.

### `render_all()`

```python
render_tabs(); render_controls(); render_canvas(); render_json()
```

Es lo que se llama tras cualquier cambio estructural. Para movimientos de puntero
se usa solo `render_canvas()`, que es la parte cara.

### `render_tabs()`

Regenera el `innerHTML` completo de `#graph-tabs`, **incluido el botón
`+ Nueva`**. Consecuencia: el botón se recrea en cada render, así que no puede
tener su propio listener — se maneja por delegación desde el contenedor
(sección 8). El botón de cerrar (`✕`) solo aparece si hay más de una pestaña.
Todo el texto pasa por `html.escape()`.

### `edge_style(edge) → (color, grosor, clase_de_animación)`

Prioridad de arriba abajo:

| Condición | Color | Grosor | Animación |
|---|---|---|---|
| `highlight == "updated"` | `#F4B45C` naranja | 4 | `edge-animated` |
| `highlight == "delivered"` | `#7A6E9E` morado | 4 | `edge-animated` |
| `highlight == "quiet"` | `#413A5E` | 1 | — |
| `id == selected_edge_id` | `#6E7CF2` azul | 4 | — |
| (por defecto) | `#6C6690` | 3 | — |

El resaltado de simulación **gana** sobre la selección.

### `render_canvas()`

El núcleo del dibujo.

```python
transform = f"translate({pan_x} {pan_y}) scale({zoom})"
for capa in ("edges-layer", "edge-labels-layer", "nodes-layer", "connection-layer"):
    by_id(capa).setAttribute("transform", transform)
```

Un solo `transform` compartido por las cuatro capas implementa zoom y pan. El
`<rect>` de la cuadrícula queda fuera, por eso el fondo punteado no se mueve.

**Aristas.** Por cada una: se resuelven los nodos (si falta alguno, se salta),
se pide la geometría y se emite un `<g>` con cuatro hijos:

```html
<g class="graph-edge edge-animated" data-edge-id="…">
  <path class="edge-hit"  stroke="transparent" stroke-width="16"/>  <!-- zona de clic -->
  <path class="edge-path" stroke="…" stroke-width="…" marker-end="url(#arrowhead)"/>
  <circle r="4" fill="#ff6b6b"/>   <!-- ancla origen -->
  <circle r="4" fill="#4ecdc4"/>   <!-- ancla destino -->
</g>
```

El `edge-hit` transparente de 16 px es lo que hace clicable una línea de 3 px.
El `marker-end` solo se emite si el grafo es dirigido.

**Etiquetas de peso.** En su propia capa, para que queden **encima de todas las
aristas** pero debajo de los nodos. El ancho de la cajita se estima con
`max(40, len(texto) * 7.4 + 14)` — aproximación del ancho de la fuente
monoespaciada.

**Nodos.** Círculo de radio 30 con el relleno de `value_color()` y el número
centrado con el color de texto que esa misma función eligió.

Todos los atributos que vienen de datos pasan por `html.escape()`.

### `render_minimap()`

Escala el grafo completo a la caja de 180×110 del `viewBox`.

```python
span_x = max(ancho_del_grafo, 120)          # mínimos para no sobre-ampliar
span_y = max(alto_del_grafo, 80)
scale  = min(158/span_x, 88/span_y)         # 158×88 = área útil con margen
offset = 11 + (área − span*scale) / 2       # centrado
```

Dibuja las aristas como líneas rectas (sin curvas ni flechas: a esa escala no se
distinguirían) y los nodos como círculos de radio `max(3, 9*scale)`.

El rectángulo de viewport se calcula invirtiendo la transformación
(`world_left = -pan_x / zoom`), se convierte a coordenadas del minimapa y se
**recorta** al rango `[1, 179] × [1, 109]` para que no se salga del marco.

> El minimapa es solo indicador: no responde a clics ni arrastres.

### `render_chart(points)`

Convierte la curva del sweep en dos `path` de SVG. El sistema de coordenadas está
fijo en el `viewBox="0 0 400 320"`:

```
left=44   right=382   top=25   bottom=293
```

```python
max_y = max(max(ciHigh), max(mean), 1e-9) * 1.08     # 8 % de aire arriba
x(p)     = left + clamp(p, 0, 1) * (right - left)
y(valor) = bottom - (valor / max_y) * (bottom - top)
```

- **Línea de media** — polilínea `M/L` sobre todos los puntos.
- **Banda de confianza** — un polígono cerrado: los `ciHigh` de izquierda a
  derecha, y los `ciLow` **en orden inverso**, cerrando con `Z`.
- **Etiquetas del eje Y** — se regeneran en 5 pasos según `max_y` (la escala es
  dinámica); las del eje X quedan fijas en `0`, `.5`, `1`.

Guarda `CHART_POINTS` y `CHART_MAX_Y` en globales para el tooltip, y oculta el
tooltip en cada actualización. Con lista vacía limpia ambos `path`.

### `on_chart_pointer_move(event)` / `hide_chart_tooltip()`

Tooltip de la gráfica. Convierte el píxel del ratón a coordenada del `viewBox`
(regla de tres con el ancho real del SVG, que es responsivo), de ahí a un valor
de `p`, y busca el punto más cercano:

```python
svg_x   = (clientX - bounds.left) * 400 / bounds.width
p_value = clamp((svg_x - 44) / (382 - 44), 0, 1)
point   = min(CHART_POINTS, key=lambda item: abs(item["p"] - p_value))
```

Muestra `p`, media e IC 95 %. La posición se ajusta para no salirse del panel
(`min(local_x + 12, ancho - 176)`).

### `render_json()` y `render_controls()`

`render_json()` — muestra/oculta `#json-panel`, alterna el texto del botón
(`Mostrar`/`Ocultar JSON`) y vuelca `json.dumps(..., indent=2, ensure_ascii=False)`.

`render_controls()` — **sincroniza toda la interfaz con `STATE`**:

| Sincroniza | Regla |
|---|---|
| `#directed-toggle` y su leyenda | `tab.directed` |
| `#algorithm-select`, `#agreed-input` | `STATE.algorithm`, `STATE.agreed` |
| `#agreed-wrapper` | Visible **solo** con AMP |
| `#simulate-btn` | Habilitado solo si hay nodos |
| `#sweep-run-btn` | Habilitado solo si hay nodos **y** no hay sweep corriendo |
| `#simulation-results` | Visible solo si hay simulación |
| `#toggle-simulation-btn` | Texto `Ver Antes` / `Ver Después` |
| `#shift-indicator`, clase `.shift-mode` | `STATE.shift_pressed` |
| Pestañas de vista + `aria-selected` | `STATE.active_view` |
| `#sweep-panel` | Visible solo en la vista `analysis` |

---

## 5. Edición del grafo

### `screen_to_world(client_x, client_y) → (x, y)`

Deshace pan y zoom. Es la conversión que usan todos los handlers de puntero:

```python
local = cliente - bounds.left/top          # a coordenadas del SVG
world = (local - pan) / zoom               # a coordenadas del grafo
```

### `add_node(x, y)`

```python
node = Node(STATE.next_node_id(), 0, x - 30, y - 30)
```

Resta el radio para **centrar el nodo bajo el cursor** (recordar que `x`/`y` es
la esquina). Lo selecciona, marca el grafo como cambiado y re-renderiza.

`add_node_at_canvas_center()` es la alternativa accesible: hace clic en la
plantilla de la barra lateral y coloca el nodo en el centro visible, sin arrastre.

### `add_edge(source_id, target_id)`

En grafos **no dirigidos** rechaza duplicados en cualquier sentido (`A–B` y `B–A`
son la misma arista). En dirigidos permite ambas direcciones — y ahí es donde
`has_reverse()` las separa en dos curvas.

El id se compone así:

```python
serial = sum(len(item.edges) for item in STATE.tabs)      # aristas de TODAS las pestañas
Edge(f"edge_local_{serial}_{STATE.node_counter}", source, target, "1")
```

Combinar ambos contadores evita colisiones entre pestañas. Al exportar, todas las
aristas se renumeran como `edge_{índice}`.

### Edición de valores y pesos

| Función | Qué edita | Validación |
|---|---|---|
| `open_node_editor(node_id)` | Valor del nodo | Debe ser `float`. Cualquier número, incluidos negativos y > 1 (el color simplemente satura). |
| `on_canvas_click(event)` | Peso de la arista | Debe estar en **`(0, 1]`**. |

`open_node_editor` sale temprano si el diálogo ya está abierto, para que un doble
clic rápido no lo reabra encima de sí mismo.

### `on_directed_change(event)`

Al pasar a **no dirigido**, colapsa las aristas duplicadas conservando la primera:

```python
seen = set()
for edge in STATE.tab.edges:
    key = frozenset((edge.source, edge.target))     # ignora la dirección
    if key not in seen:
        seen.add(key); unique.append(edge)
```

Es una operación **con pérdida**: volver a dirigido no recupera las aristas
eliminadas.

### Borrado

`clear_graph()` intenta `window.confirm` y cae al diálogo propio si lanza;
`clear_graph_confirmed()` vacía nodos, aristas y simulación.

El borrado selectivo vive dentro de `on_key_down` (`Delete`/`Backspace`) y
**elimina también las aristas huérfanas** del nodo borrado:

```python
STATE.tab.nodes = [n for n in nodes if n.id != node_id]
STATE.tab.edges = [e for e in edges if e.source != node_id and e.target != node_id]
```

### `mark_graph_changed()`

```python
if STATE.simulation is None and STATE.tab.nodes:
    STATE.initial_graph = graph_snapshot()
```

Se llama tras **cada** edición. Mientras no haya simulación activa, mantiene
actualizado el snapshot que sirve de punto de partida y de destino del botón
«Resetear». Una vez que hay simulación, deja de actualizarse: el estado inicial
queda congelado.

---

## 6. La máquina de estados del puntero

Tres handlers (`pointerdown`/`move`/`up`) implementan **tres modos excluyentes**.
Es la parte más sutil del archivo.

### `on_canvas_pointer_down(event)`

```
¿el objetivo está dentro de [data-node-id]?
    ├─ Shift pulsado  → modo CONNECTION  + setPointerCapture
    └─ si no          → modo DRAG        + setPointerCapture (moved = False)
¿está dentro de [data-edge-id]?
    └─ selecciona la arista y sale
si no (área vacía)
    ├─ limpia la selección
    └─ Espacio pulsado → modo PAN
```

`setPointerCapture` es lo que permite arrastrar **fuera del SVG** sin perder el
puntero.

### `on_canvas_pointer_move(event)` — el umbral de 6 px

```python
if not DRAG["moved"]:
    distance = hypot(clientX - start_x, clientY - start_y)
    if distance < 6.0:
        return          # todavía no es un arrastre
    DRAG["moved"] = True
```

El comentario del código explica el porqué: un ratón real emite micro-movimientos
entre los dos clics de un doble clic. Sin el umbral, el primer `pointermove`
dispararía `render_canvas()`, que **reemplaza el `innerHTML`** — y al desaparecer
el elemento original el navegador ya no puede emitir el `dblclick`. El umbral
mantiene el SVG intacto hasta que el arrastre es inequívoco.

En modo `CONNECTION` dibuja la línea provisional en `#connection-layer`; en modo
`PAN` actualiza `pan_x`/`pan_y`.

### `on_canvas_pointer_up(event)` — el doble clic manual

Si el arrastre nunca superó el umbral, se trata como clic y se aplica una
detección de doble clic **propia**, como respaldo del `dblclick` nativo:

```python
if previous_id == node_id and elapsed <= 750.0 and distance <= 14.0:
    open_node_editor(node_id)          # segundo clic sobre el mismo nodo
else:
    LAST_NODE_CLICK.update({"node": node_id, "time": now, "x": …, "y": …})
```

Ventana: **750 ms y 14 px**. `on_canvas_double_click` (el `dblclick` nativo) hace
lo mismo y limpia `LAST_NODE_CLICK` para que ambos caminos no se dupliquen.

Al soltar en modo `CONNECTION` se resuelve el destino con
`document.elementFromPoint(clientX, clientY)` — hace falta porque el puntero está
capturado por el SVG y el `event.target` no sería el nodo de destino.

### Zoom y encuadre

| Función | Detalle |
|---|---|
| `on_wheel(event)` | Factor `1.12` por muesca. **Mantiene fijo el punto bajo el cursor**: convierte a coordenadas de mundo antes de cambiar el zoom y recalcula el pan después. |
| `zoom_by(factor)` | Igual pero anclado al centro del canvas. Botones `+` (1.2) y `−` (1/1.2). |
| `fit_view()` | Delega en `geometry.fit_transform`. |

Rango de zoom manual: `[0.2, 3.0]`. El del encuadre automático: `[0.2, 2.0]`.

### Teclado

| Tecla | Efecto |
|---|---|
| `Shift` | Modo conectar (`shift_pressed`, indicador y cursor `crosshair`). Al soltar, cancela una conexión en curso. |
| `Espacio` | Modo desplazar. `preventDefault` para que no haga scroll. |
| `Delete` / `Backspace` | Borra el nodo o la arista seleccionada. |

Los tres se ignoran si el foco está en un `input`, `select` o `textarea`
(`editing`), para no interferir al escribir.

---

## 7. Pestañas, importación y exportación

| Función | Detalle |
|---|---|
| `on_tabs_click(event)` | **Delegación de eventos** con `closest()`: distingue cerrar (`[data-close-tab]`), nueva (`#new-tab-btn`) y seleccionar (`[data-tab-id]`). Necesario porque `render_tabs()` recrea el marcado. |
| `new_tab()` | Crea `Grafo {n+1}` y la activa. |
| `close_tab(tab_id)` | Impide cerrar la última (`notify`). Si se cierra la activa, salta a la primera. |
| `export_json()` | `Blob` → `URL.createObjectURL` → `<a download="graph.json">` → `click()`. Limpia con `revokeObjectURL` a los 750 ms. |
| `import_json_click()` | Dispara el `#file-input` oculto. |
| `on_file_change(event)` | `file.text().then(...)`. **Resetea `event.target.value`** para poder reimportar el mismo archivo. |
| `finish_import(text, filename)` | Ver abajo. |

### `finish_import`

1. Valida que exista un array `nodes` (si no, `notify` y aborta).
2. Construye `Node`s; los que no traen `position` reciben una posición aleatoria
   en un cuadro de 500×500.
3. Construye `Edge`s renumeradas.
4. **Crea una pestaña nueva** (no reemplaza la actual), nombrada como el archivo
   sin la extensión `.json`.
5. **Ajusta `node_counter`** al máximo `node_N` encontrado + 1, para que los ids
   futuros no colisionen con los importados.
6. Limpia la simulación y re-renderiza.

Todo va dentro de un `try/except` que reporta con `notify()`.

---

## 8. Simulación individual

### `simulate()`

```python
before = graph_snapshot(STATE.initial_graph or STATE.tab)
if STATE.initial_graph is None:
    STATE.initial_graph = before.clone()

result = run_round(before.graph_data(), algorithm=STATE.algorithm, agreed=STATE.agreed)

after = before.clone()
for index, node in enumerate(after.nodes):
    node.value = result["values"][index]
apply_propagation(after.edges, result["propagation"])

STATE.simulation = SimulationState(before, after, propagation)
replacement = after.clone(); replacement.id, replacement.name = STATE.tab.id, STATE.tab.name
STATE.tabs[STATE.tabs.index(STATE.tab)] = replacement
STATE.show_after = True
```

**Siempre parte del grafo inicial.** Pulsar «Simular» dos veces son dos rondas
independientes desde el mismo punto de partida (con azar distinto), **no** dos
rondas encadenadas. Para encadenar existe `engine.run_simulation(rounds=N)`, que
la interfaz no expone.

La pestaña activa se **reemplaza** por un clon con el resultado, conservando `id`
y `name`. Toda la función está envuelta en `try/except` que reporta con `notify()`
y además escribe en `window.console.error`.

### `apply_propagation(edges, propagation)`

Para cada arista busca un registro que coincida en `(source, target)`:

| Encuentra | `highlight` |
|---|---|
| Con `updated_value: True` | `updated` (naranja) |
| Con `updated_value: False` | `delivered` (morado) |
| Nada | `quiet` (apagada) |

**Limitación conocida:** la búsqueda es por par `(source, target)`, no por id de
arista. Si existen dos aristas paralelas entre los mismos nodos, ambas reciben el
mismo resaltado.

### `toggle_simulation()` y `reset_simulation()`

`toggle_simulation` alterna la pestaña activa entre `simulation.before` y
`simulation.after`. Al volver al "antes" **reaplica los highlights**, para que se
vea qué aristas transmitieron partiendo del estado original.

`reset_simulation` restaura `initial_graph`, borra `STATE.simulation` y vuelve al
estado pre-simulación.

---

## 9. El puente con el Worker del sweep

| Función | Detalle |
|---|---|
| `positive_int(element_id, minimum)` | Lee un input numérico; ante basura devuelve el mínimo. **Solo aplica el mínimo**, no el máximo (ver [04 · Validación](04-barrido-sweep.md)). |
| `set_sweep_inputs_disabled(disabled)` | Deshabilita los cuatro inputs y conmuta los botones Ejecutar/Detener. |
| `start_sweep()` | Ver abajo. |
| `stop_sweep()` | `WORKER.terminate()` + restaura la interfaz + resetea el texto de estado. |
| `finish_sweep()` | Igual, pero **sin** tocar el texto de estado (para no borrar el mensaje de error o de completado). |

### `start_sweep()`

```python
params = {
    "graph": current_graph_json(),
    "algorithm": STATE.algorithm,
    "agreed": STATE.agreed,
    "numPPoints":           positive_int("p-points-input", 2),
    "totalSimulationsPerP": positive_int("simulations-input", 10),
    "iterations":           positive_int("iterations-input", 1),
    "rounds":               positive_int("rounds-input", 1),
}
WORKER = Worker.new("./js/sweep-worker.js", to_js({"type": "module"}, dict_converter=Object.fromEntries))
WORKER.addEventListener("message", retain(on_message))
WORKER.addEventListener("error",   retain(on_error))
WORKER.postMessage(to_js({"type": "run", "params": params}, dict_converter=Object.fromEntries))
```

El Worker se **crea y destruye por cada sweep**. Simplifica la cancelación
(`terminate()` es inmediato y no deja estado colgando) a costa de reinstanciar
Pyodide cada vez.

### `on_message(event)`

```python
data = event.data.to_py() if hasattr(event.data, "to_py") else event.data
```

Cada tipo de evento:

| `type` | Acción |
|---|---|
| `iteration` | `render_chart(points)` + texto `Iteración k/N — S sims/p` (con `— completo` si `done`). |
| `end` | `finish_sweep()`. |
| `error` | Mensaje escapado con `html.escape` en `#sweep-status` + `finish_sweep()`. |
| `loading` | Texto "Cargando Python y NumPy en el worker…". |
| `start` | Se reconoce pero no dispara ninguna acción. |

`on_error` cubre los fallos del Worker que no llegan como mensaje (por ejemplo un
error de sintaxis en el propio `.js`).

---

## 10. Arranque

### `bind_events()`

Registra unos 38 listeners de golpe. Agrupados:

| Grupo | Eventos |
|---|---|
| Pestañas | `click` en `#graph-tabs` (delegado) |
| Plantilla de nodo | `dragstart`, `click` |
| Canvas | `dragover`, `drop`, `pointerdown/move/up/cancel`, `dblclick`, `click`, `wheel` |
| Ventana | `keydown`, `keyup`, `resize` → `render_canvas()` |
| Barra lateral | `change` de dirigido, JSON, importar, exportar, limpiar, algoritmo, `agreed` |
| Simulación | Simular, alternar, resetear |
| Vistas y zoom | Editor/Análisis, `+`, `−`, `⌖` |
| Sweep | Ejecutar, Detener, `pointermove`/`pointerleave` de la gráfica |
| Diálogo | Cancelar, Aceptar, `keydown` del input |

`pointercancel` se enlaza al mismo handler que `pointerup` para que un gesto
interrumpido por el sistema no deje `DRAG`/`CONNECTION` colgados.

### `main()`

```python
def main() -> None:
    bind_events()
    render_all()
    fit_view()
    window.graphSimulatorReady = True

if __name__ == "__main__":
    main()
```

`window.graphSimulatorReady` es una bandera pensada para comprobaciones
automatizadas desde el navegador: permite esperar a que la aplicación esté lista
sin adivinar tiempos. El `if __name__` es lo que dispara el arranque cuando
`bootstrap.js` ejecuta el archivo con `run_name="__main__"`.

---

← [Índice](../DOCUMENTACION.md) · Siguiente: [06 · Formatos, flujos y detalles](06-formatos-flujos-y-detalles.md)
