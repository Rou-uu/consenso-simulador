← [Índice](../DOCUMENTACION.md) · Anterior: [05 · Controlador](05-controlador-app.md)

# 06 · Formatos, flujos y detalles

Referencia transversal: el formato JSON, los contratos entre capas, las
secuencias completas de las operaciones principales, cabos sueltos verificados
en el código y un glosario.

---

## 1. Formato JSON del grafo

Es el mismo del proyecto original. Sirve para **tres cosas a la vez**: el visor
«Mostrar JSON», el import/export y la entrada del motor.

```json
{
  "directed": true,
  "nodes": [
    { "id": "node_0", "value": 1,   "position": { "x": 120, "y": 80  } },
    { "id": "node_1", "value": 0,   "position": { "x": 320, "y": 200 } }
  ],
  "edges": [
    { "id": "edge_0", "source": "node_0", "target": "node_1", "weight": "0.5" }
  ]
}
```

| Campo | Tipo | Notas |
|---|---|---|
| `directed` | `bool` | Si es `false`, cada arista se refleja en la matriz. |
| `nodes[].id` | `str` | **Único.** El orden del array define el orden de filas/columnas de la matriz. |
| `nodes[].value` | `number` | `null` se normaliza a `0` al importar. |
| `nodes[].position` | `{x, y}` | **Esquina superior izquierda**, no el centro. Opcional al importar (se asigna al azar). |
| `edges[].id` | `str` | Se **renumera** como `edge_{índice}` al importar y al exportar. |
| `edges[].source` / `target` | `str` | Deben existir en `nodes` o el motor lanza `KeyError`. |
| `edges[].weight` | `str` | **String**, no número. El motor lo convierte con `float()`. La UI valida `(0, 1]`. |

### Tolerancias al importar

| Situación | Qué pasa |
|---|---|
| Falta `nodes` o no es lista | `notify()` con error, no se importa nada. |
| Falta `edges` | Se trata como lista vacía. |
| Falta `directed` | Se asume `true`. |
| Falta `position` | Posición aleatoria en un cuadro de 500×500. |
| `value` es `null` | Se convierte a `0`. |
| Ids `node_N` presentes | `node_counter` se ajusta a `max(N) + 1`. |

---

## 2. Contratos entre capas

Solo hay **cuatro fronteras** en todo el proyecto. Conocerlas basta para
orientarse:

```
┌─ JS ─────────────────────────────────────────────────────────┐
│ bootstrap.js ──(1)── monta .py y ejecuta app.py               │
└──────────────────────────────────────────────────────────────┘
┌─ Python (hilo principal) ────────────────────────────────────┐
│ app.py ──(2)── GraphTab.graph_data() ──▶ engine.run_round()   │
│ app.py ──(3)── postMessage({type:"run", params}) ──▶ Worker   │
└──────────────────────────────────────────────────────────────┘
┌─ Python (Worker) ────────────────────────────────────────────┐
│ sweep-worker.js ──(4)── iter_sweep() ──▶ postMessage(evento)  │
└──────────────────────────────────────────────────────────────┘
```

| # | Frontera | Contrato |
|---|---|---|
| 1 | JS → Python | Archivos montados en `/python`, `sys.path`, `run_name="__main__"`. |
| 2 | Interfaz → motor | El JSON del grafo (sección 1). Vuelve el `dict` de `run_round` ([03](03-motor-simulacion.md)). |
| 3 | Hilo principal → Worker | `{type:"run", params:{graph, algorithm, agreed, numPPoints, totalSimulationsPerP, iterations, rounds}}`. |
| 4 | Worker → hilo principal | Eventos `start` / `iteration` / `cancelled` / `error`, más `end` que añade el propio worker ([04](04-barrido-sweep.md)). |

**Regla derivada:** todo lo que cruce las fronteras 2, 3 y 4 debe ser
serializable — `dict`, `list`, `str`, `int`, `float`, `bool`, `None`. Nada de
`ndarray` ni escalares NumPy. Por eso `run_round` e `iter_sweep` convierten
explícitamente con `float(...)`, `int(...)`, `.tolist()`.

---

## 3. Secuencias completas

### Crear un nodo por arrastre

```
dragstart en #node-template   → dataTransfer.setData("application/graph-node")
dragover  en #graph-canvas    → preventDefault (habilita el drop)
drop      en #graph-canvas    → screen_to_world(clientX, clientY)
                              → add_node(x, y)      # resta 30 para centrar
                              → mark_graph_changed()
                              → render_all()
```

### Crear una arista

```
keydown Shift          → STATE.shift_pressed = True → render_controls()
                         (aparece #shift-indicator, cursor crosshair)
pointerdown en nodo A  → CONNECTION = {source: "node_0"} + setPointerCapture
pointermove            → dibuja <path> provisional en #connection-layer
pointerup              → document.elementFromPoint(...) → nodo B
                       → add_edge("node_0", "node_1")
                       → render_all()
keyup Shift            → limpia CONNECTION y #connection-layer
```

### Editar el valor de un nodo (doble clic)

```
pointerdown  → DRAG = {..., moved: False}
pointermove  → distancia < 6 px ⟹ return (NO se re-renderiza)
pointerup    → moved == False ⟹ se guarda LAST_NODE_CLICK
… segundo clic dentro de 750 ms y 14 px …
pointerup    → coincide ⟹ open_node_editor("node_0")
             (en paralelo, el dblclick nativo llega al mismo sitio)
Enter        → submit_dialog() → commit(valor)
                 ├─ float() falla ⟹ dialog_error(), sigue abierto
                 └─ ok ⟹ node.value = … → render_all() → cierra
```

### Simular una ronda

```
clic en #simulate-btn
  → simulate()
  → before = STATE.initial_graph (o el grafo actual la primera vez)
  → engine.run_round(before.graph_data(), algorithm, agreed)
       get_adj → who_gets_message → fv/amp → propagation_from_round
  → after = before.clone() con los valores nuevos
  → apply_propagation(after.edges, propagation)   # updated / delivered / quiet
  → STATE.simulation = SimulationState(before, after, propagation)
  → la pestaña activa se reemplaza por `after`
  → STATE.show_after = True → render_all()
```

Después:

```
«Ver Antes»  → toggle_simulation() → pestaña = before + highlights
«Resetear»   → reset_simulation()  → pestaña = initial_graph, simulación borrada
```

### Ejecutar un sweep

```
clic en #sweep-run-btn
  → start_sweep(): params, sweep_running = True, inputs deshabilitados
  → render_chart([]) limpia la gráfica
  → Worker.new("./js/sweep-worker.js", {type:"module"})
  → postMessage({type:"run", params})
════════ Worker ════════
  → createRuntime(): Pyodide + NumPy + simulation/*        (segundos)
  → iter_sweep(...)
       yield start                              → postMessage
       iteración 0: N puntos de p × S sims      → yield iteration → postMessage
       iteración 1: …                           → yield iteration → postMessage
       … iteración 49 (done: True)
  → postMessage({type:"end"})
════════ hilo principal ════════
  → on_message("iteration") → render_chart(points) + «Iteración 1/50 — 100 sims/p»
  → on_message("end")       → finish_sweep(): worker terminado, inputs activos
```

Si se pulsa «Detener»: `stop_sweep()` → `WORKER.terminate()` inmediato. El evento
`cancelled` del generador no llega a usarse en este camino.

---

## 4. Cabos sueltos verificados

Hechos comprobados leyendo el código. Ninguno rompe la aplicación; se listan para
que nadie pierda tiempo buscándolos.

### La instrucción de "snap a múltiplos de 10" no está implementada

`index.html:85` lista:

> «Shift mientras arrastras un nodo: snap a múltiplos de 10»

No existe tal comportamiento en el código. En `on_canvas_pointer_move` el
arrastre asigna la posición sin redondear, y además `Shift` + `pointerdown` sobre
un nodo entra en modo **conectar**, no en modo arrastrar — así que la combinación
descrita no es alcanzable. Es una instrucción heredada del proyecto de
referencia. Hay que implementarla o quitarla de la lista.

### `AppState.capture_initial()` no se usa

Definida en `models.py:129`, sin una sola llamada en el proyecto.
`app.mark_graph_changed()` hace exactamente lo mismo en línea.

### Hay reglas CSS que nunca se aplican

Definidas en `css/app.css` pero jamás añadidas por Python ni JS:

| Selector | Por qué no se aplica |
|---|---|
| `.edge-path.is-selected`, `.is-updated`, `.is-delivered`, `.is-idle` | `render_canvas()` escribe `stroke` y `stroke-width` **como atributos en línea**, con los valores que devuelve `edge_style()`. |
| `.edge-anchor-source`, `.edge-anchor-target` | Los círculos de ancla llevan `fill` en línea. |
| `.python-loader.is-ready` | `bootstrap.js` usa `dataset.state` y `hidden`, no esa clase. |

Los colores están **duplicados** entre `edge_style()` (Python) y esas reglas CSS.
Si se cambian los de Python, las reglas muertas quedan desincronizadas.

### El evento `loading` del sweep no lo emite nadie

`app.on_message` tiene una rama para `{"type": "loading"}`, pero
`sweep-worker.js` nunca lo publica. El mensaje "Cargando motor Python del
worker…" que sí se ve lo escribe `start_sweep()` antes de crear el Worker.
Del mismo modo, la rama de `start` se reconoce pero no hace nada, y `cancelled`
no tiene rama porque la UI cancela con `terminate()`.

### El máximo de los `<input>` del sweep no se aplica en el cliente

`positive_int()` solo impone el mínimo. Un valor por encima del máximo del HTML
llega hasta `_prepare_sweep`, que lanza `ValueError`; se ve como un mensaje de
error en `#sweep-status` en vez de recortarse. Funciona, pero el error aparece
tras arrancar el Worker (unos segundos después del clic).

### `apply_propagation` no distingue aristas paralelas

Empareja por `(source, target)`, no por id. Dos aristas dirigidas entre el mismo
par de nodos reciben el mismo resaltado aunque solo una haya transmitido.

### Self-loops en grafos no dirigidos disparan a la mitad de su peso

Consecuencia de `upper + upper.T` con `np.triu`, que incluye la diagonal y por
tanto la duplica. Comportamiento heredado del backend; ver
[03 · El sorteo](03-motor-simulacion.md).

### La mayor parte de la API pública del motor no la usa la aplicación

De todo `simulation/`, la app consume **`run_round`** e **`iter_sweep`**. El
resto (`simulate_graph`, `run_simulation`, `run_sweep`, `async_iter_sweep`,
`CancellationToken`, y las primitivas de bajo nivel) existe por paridad con el
backend original y para poder ejercitar el motor desde Python de escritorio. No
es código muerto, pero tampoco está en el camino de ejecución del navegador.

### El README describe `bootstrap.js` como el único cargador

`js/sweep-worker.js` también levanta una instancia propia de Pyodide. La frase
del README se refiere al cargador del runtime **principal**.

---

## 5. Probar el motor sin navegador

Al no depender de `js` ni del DOM, `simulation/` se puede ejercitar directamente:

```bash
cd python && python3 -c "
from simulation.engine import run_round, run_simulation
import numpy as np

grafo = {
    'directed': True,
    'nodes': [{'id': 'A', 'value': 0}, {'id': 'B', 'value': 1}, {'id': 'C', 'value': 0}],
    'edges': [{'source': 'A', 'target': 'B', 'weight': '0.5'},
              {'source': 'B', 'target': 'C', 'weight': '1'}],
}

np.random.seed(42)                      # reproducible
r = run_round(grafo, algorithm='fv')
print(r['values'], r['discrepancy'])
print(r['propagation'])
"
```

Para el barrido:

```bash
cd python && python3 -c "
from simulation.sweep import run_sweep
grafo = {'directed': True,
         'nodes': [{'id': 'A', 'value': 0}, {'id': 'B', 'value': 1}],
         'edges': [{'source': 'A', 'target': 'B', 'weight': '1'}]}
final = run_sweep(grafo, 'fv', num_p_points=5, total_simulations_per_p=100, iterations=2)
for punto in final['points']:
    print(punto)
"
```

Requisitos: Python 3.11+ y NumPy. `models.py` y `geometry.py` también se importan
sin problema; `app.py` **no** (falla al importar `js`).

---

## 6. Glosario

| Término | Significado |
|---|---|
| **Ronda** | Una aplicación del par sorteo + decisión sobre todo el grafo. La actualización es simultánea. |
| **Discrepancia** | `max(valores) − min(valores)`. La métrica central; `0` = consenso. |
| **FV (Flip Value)** | Un nodo que recibe mensajes elige uno al azar y copia su valor. |
| **AMP (Agreement)** | Un nodo que recibe cualquier mensaje adopta el valor fijo `agreed`. |
| **`agreed`** | El valor de consenso que impone AMP. Solo visible en la UI con AMP seleccionado. |
| **Sweep / barrido** | Recorrer `p` de 0 a 1 midiendo la discrepancia media resultante. |
| **`p`** | Probabilidad uniforme que sustituye a **todos** los pesos durante el barrido. |
| **Iteración** (sweep) | Una pasada completa por todos los valores de `p`. Refina la curva. |
| **`rounds`** (sweep) | Cuántas rondas se encadenan dentro de cada simulación del barrido. |
| **Réplica / simulación** | Una corrida independiente. En el sweep son el eje 0 de los tensores. |
| **Propagación** | La lista de mensajes efectivamente enviados en una ronda; lo que colorea las aristas. |
| **`highlight`** | Marca visual de una arista: `updated` / `delivered` / `quiet`. |
| **Pyodide** | Distribución de CPython compilada a WebAssembly. Fijada en `314.0.2`. |
| **Proxy** | Envoltorio que permite a JS llamar a una función de Python. Debe retenerse (`retain()`). |
| **MEMFS** | Sistema de archivos virtual en memoria de Pyodide, donde se montan los `.py`. |

---

← [Índice](../DOCUMENTACION.md)
