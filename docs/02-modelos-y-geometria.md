← [Índice](../DOCUMENTACION.md) · Anterior: [01 · Runtime y arranque](01-runtime-y-arranque.md)

# 02 · Modelos y geometría

Los dos módulos Python **puros** de la capa de interfaz: no importan `js`, no
tocan el DOM y se pueden probar desde Python de escritorio.

---

# `python/models.py` — el estado

137 líneas de dataclasses. Es la **fuente de verdad**: el SVG se regenera desde
aquí, nunca al revés.

## `Node`

```python
@dataclass
class Node:
    id: str
    value: int | float = 0
    x: float = 0.0          # esquina superior izquierda, NO el centro
    y: float = 0.0
```

**`x`/`y` son la esquina, no el centro.** El nodo se dibuja como un círculo de
radio 30 en `(x+30, y+30)`. Esto se hereda del original (ReactFlow posiciona por
esquina) y explica los `+30`/`−30` repartidos por el código:

- `geometry.node_center()` suma el radio,
- `app.add_node(x, y)` resta 30 para **centrar el nodo bajo el cursor**,
- `render_minimap()` suma 30 al calcular puntos.

| Método | Detalle |
|---|---|
| `from_json(data, fallback_x, fallback_y)` | Lee `data["position"]` si existe; si no, usa los fallbacks (`app.finish_import` pasa `random.random()*500`, así que un JSON sin posiciones queda esparcido en un cuadro de 500×500). `value` nulo se normaliza a `0`. El `id` se fuerza a `str`. |
| `to_json()` | `{"id", "value", "position": {"x", "y"}}`. |

## `Edge`

```python
@dataclass
class Edge:
    id: str
    source: str
    target: str
    weight: str = "1"                # ← string, no float
    highlight: str | None = None     # updated | delivered | quiet | None
```

Dos campos merecen explicación:

**`weight` es un `str`.** Se conserva el texto tal como lo escribió la persona
usuaria y se convierte con `float()` recién dentro del motor (`get_adj`). Ventaja:
`"0.50"` se muestra como `"0.50"` y no como `0.5`. Consecuencia: el JSON exporta
pesos entre comillas, igual que el proyecto original.

**`highlight` es puramente visual** y lo escribe `app.apply_propagation()` tras
simular. `edge_style()` lo traduce a color:

| `highlight` | Significado | Color |
|---|---|---|
| `"updated"` | La arista transmitió **y** su valor fue el adoptado. | `#F4B45C` naranja, animada |
| `"delivered"` | Transmitió pero el target eligió a otro emisor. | `#7A6E9E` morado, animada |
| `"quiet"` | No disparó en esta ronda. | `#413A5E` apagada, 1 px |
| `None` | Sin simulación. | `#6C6690` normal, 3 px |

**Ids inestables:** `from_json(data, index)` y `to_json(index)` fabrican
`f"edge_{index}"` a partir de la **posición en la lista**, ignorando el `id` que
traía el archivo. Las aristas creadas a mano llevan otro formato
(`edge_local_{serial}_{contador}`, ver [05](05-controlador-app.md)). O sea: los
ids de arista son estables **dentro** de una sesión pero se renumeran al
exportar. Los ids de nodo sí se respetan.

## `GraphTab`

```python
@dataclass
class GraphTab:
    id: str
    name: str
    nodes: list[Node]
    edges: list[Edge]
    directed: bool = True
```

| Método | Detalle |
|---|---|
| `graph_data()` | Produce el JSON canónico `{"directed", "nodes", "edges"}`. **Es la única frontera hacia el motor**: `run_round` e `iter_sweep` reciben exactamente esto. |
| `clone()` | `deepcopy(self)` — copia profunda, nodos y aristas incluidos. Es la base del "antes/después". |

## `SimulationState`

```python
@dataclass
class SimulationState:
    before: GraphTab                 # grafo antes de la ronda
    after: GraphTab                  # grafo después, con highlights aplicados
    propagation: list[dict]          # registros de engine.propagation_from_round
```

Guardar el par completo (y no un diff) es lo que permite que
`toggle_simulation()` alterne cuantas veces quiera sin recalcular nada.

## `AppState`

El objeto global único. `app.py` crea `STATE = AppState()` y todo lo demás lo lee
o lo muta.

| Grupo | Campos | Para qué |
|---|---|---|
| Documento | `tabs`, `active_tab_id` | Pestañas de grafos abiertas. |
| Navegación | `active_view` (`editor`/`analysis`), `show_json` | Qué se ve. |
| Simulación | `algorithm` (`fv`/`amp`), `agreed`, `initial_graph`, `simulation`, `show_after` | Configuración y resultado. |
| Teclado | `shift_pressed`, `space_pressed` | Modo conectar / modo desplazar. |
| Selección | `selected_node_id`, `selected_edge_id` | Qué está resaltado y qué borra `Delete`. |
| Viewport | `zoom`, `pan_x`, `pan_y` | Transformación del canvas. |
| Contadores | `node_counter`, `tab_counter` | Generadores de id. |
| Sweep | `sweep_running` | Bloquea el botón mientras corre. |

**Invariante:** `selected_node_id` y `selected_edge_id` son mutuamente
excluyentes. Seleccionar uno pone el otro en `None`.

### Métodos

```python
@property
def tab(self) -> GraphTab:
    for tab in self.tabs:
        if tab.id == self.active_tab_id:
            return tab
    self.active_tab_id = self.tabs[0].id   # autorreparación
    return self.tabs[0]
```

La propiedad `tab` es **la forma de acceder al grafo activo** en todo el código
(`STATE.tab.nodes`, `STATE.tab.edges`). Si `active_tab_id` apunta a una pestaña
que ya no existe, se repara sola en vez de lanzar.

| Método | Detalle |
|---|---|
| `next_node_id()` | `node_0`, `node_1`, … incrementando `node_counter`. |
| `next_tab_id()` | `tab_1`, `tab_2`, … (`tab_0` viene del `default_factory`). |
| `capture_initial()` | Guarda el snapshot inicial si no hay simulación y hay nodos. **Actualmente sin uso**: `app.mark_graph_changed()` hace lo mismo en línea. Ver [06](06-formatos-flujos-y-detalles.md). |
| `clear_simulation()` | Pone `initial_graph`, `simulation` en `None` y `show_after` en `False`. |

---

# `python/geometry.py` — el dibujo

110 líneas de matemática pura. Recibe `Node`/`Edge` y devuelve números y cadenas
`path` de SVG. **No sabe nada del DOM.**

## Constantes

```python
NODE_RADIUS = 30.0
V0 = (110, 124, 242)   # azul  → valor 0   (= --v0 del CSS)
V1 = (244, 180,  92)   # naranja → valor 1 (= --v1 del CSS)
```

## `clamp(value, low, high)`

Recorte trivial: `max(low, min(high, value))`. Se usa para el color y para el
zoom del encuadre.

## `value_color(value) → (relleno, color_de_texto)`

Interpola el color del nodo y **elige el color del texto por luminancia**, para
que el número siempre contraste con su fondo.

```python
amount = clamp(float(value), 0.0, 1.0)               # NaN y no-numéricos → 0.0
rgb = tuple(round(a + (b - a) * amount) for a, b in zip(V0, V1))
luminance = (0.299*R + 0.587*G + 0.114*B) / 255      # luma ITU-R BT.601
text = "#1A1524" if luminance > 0.6 else "#F1EDF8"   # oscuro sobre claro
```

| Valor | Relleno | Texto |
|---|---|---|
| `0` (o menor) | `rgb(110, 124, 242)` azul | claro `#F1EDF8` |
| `0.5` | `rgb(177, 152, 167)` malva | claro |
| `1` (o mayor) | `rgb(244, 180, 92)` naranja | **oscuro** `#1A1524` |
| `NaN`, `None`, texto | azul (tratado como 0) | claro |

Es **robusta a basura**: cualquier `TypeError`/`ValueError` cae en `amount = 0.0`
en vez de romper el render.

## `node_center(node) → (cx, cy)`

`(node.x + 30, node.y + 30)`. Ver la nota sobre esquina vs. centro más arriba.

## `edge_geometry(source, target, curved=False) → dict`

El corazón del dibujo de aristas. Devuelve:

```python
{"path", "source_x", "source_y", "target_x", "target_y", "label_x", "label_y"}
```

- `path` — la `d` del `<path>` SVG.
- `source_x/y`, `target_x/y` — anclas donde `app.py` dibuja los puntitos
  (rojo `#ff6b6b` en el origen, turquesa `#4ecdc4` en el destino).
- `label_x/y` — dónde va la cajita del peso.

### Los tres casos

**1 · Self-loop** (`distancia < 1e-9`, es decir `source is target`)

Curva cúbica compacta **por encima** del nodo:

```
M (cx−18, cy−24) C (cx−48, cy−78) (cx+48, cy−78) (cx+18, cy−24)
etiqueta en (cx, cy−62)
```

**2 · Recta** (`curved=False`)

```python
ux, uy = dx/distancia, dy/distancia                       # vector unitario
source_x, source_y = sx + ux*30, sy + uy*30               # sale del borde
target_x, target_y = tx - ux*30, ty - uy*30               # llega al borde
path = f"M {source_x},{source_y} L {target_x},{target_y}"
etiqueta en el punto medio
```

El recorte de 30 px en ambos extremos es lo que evita que la línea entre al
círculo y que **la punta de flecha quede tapada** por el nodo destino.

**3 · Curva** (`curved=True`)

Bézier cuadrática con el control desplazado 20 px perpendicular al segmento:

```python
perp_x, perp_y = -uy, ux                                  # normal al vector
control = punto_medio + perp * 20
path = f"M {source} Q {control} {target}"
etiqueta sobre el punto de control
```

### Cuándo se curva

`app.render_canvas()` decide:

```python
curved = tab.directed and has_reverse(edge, tab.edges)
```

O sea: **solo en grafos dirigidos y solo si existe la arista de vuelta.** Así
`A→B` y `B→A` se separan en dos arcos visibles en vez de superponerse. En grafos
no dirigidos nunca se curva (no hay pares opuestos: `add_edge` los impide).

## `has_reverse(edge, edges) → bool`

```python
any(other.id != edge.id and other.source == edge.target
                        and other.target == edge.source
    for other in edges)
```

Complejidad O(E) por arista, O(E²) por render. Irrelevante a la escala del
editor.

## `fit_transform(nodes, width, height) → (zoom, pan_x, pan_y)`

Calcula la transformación que encuadra **todo** el grafo:

```python
if not nodes or width <= 0 or height <= 0:
    return 1.0, 0.0, 0.0                      # caso vacío: identidad

# 1. Caja del grafo (sumando el diámetro para incluir el círculo completo)
min_x, min_y = min(n.x), min(n.y)
max_x, max_y = max(n.x + 60), max(n.y + 60)
graph_w, graph_h = max(max_x - min_x, 1), max(max_y - min_y, 1)

# 2. Zoom que cabe en ambos ejes, con 80 px de margen
zoom = clamp(min((width - 80)/graph_w, (height - 80)/graph_h), 0.2, 2.0)

# 3. Pan que centra la caja escalada
pan_x = (width - graph_w*zoom)/2 - min_x*zoom
pan_y = (height - graph_h*zoom)/2 - min_y*zoom
```

**Nota:** el zoom del encuadre está limitado a `[0.2, 2.0]`, pero el zoom manual
(rueda y botones, en `app.py`) llega a `[0.2, 3.0]`. Un grafo de un solo nodo se
encuadra a 2× y no a 3×.

Se llama desde `app.fit_view()` (botón `⌖`) y una vez en `main()`, al arrancar.

---

← [Índice](../DOCUMENTACION.md) · Siguiente: [03 · Motor de simulación](03-motor-simulacion.md)
