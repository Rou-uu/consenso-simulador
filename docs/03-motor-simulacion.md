← [Índice](../DOCUMENTACION.md) · Anterior: [02 · Modelos y geometría](02-modelos-y-geometria.md)

# 03 · Motor de simulación (`simulation/engine.py`)

521 líneas. Port directo de la semántica del backend original (`../simulador/back/api.py`),
sin FastAPI y sin DOM. Ejecuta **una ronda** de propagación sobre un grafo.

Corre en el **hilo principal** (una ronda es barata). El barrido masivo usa otro
módulo: [04 · sweep](04-barrido-sweep.md).

---

## 1. El modelo, en palabras

Una **ronda** tiene dos fases:

1. **Sorteo.** Cada arista con peso `w` transmite con probabilidad `w`,
   independientemente de las demás.
2. **Decisión.** Cada nodo que recibió al menos un mensaje actualiza su valor
   según el algoritmo (FV o AMP). Los que no recibieron nada se quedan igual.

**La actualización es simultánea.** Todos los nodos leen los valores *anteriores*
de sus vecinos. No hay orden de evaluación: si `A→B` y `B→C` disparan en la misma
ronda, `C` recibe el valor **viejo** de `B`, no el que `B` acaba de adoptar.

## 2. Las tres convenciones críticas

Están documentadas en el docstring del módulo porque **son fáciles de romper**:

| Convención | Detalle |
|---|---|
| `matrix[source, target]` | La matriz de adyacencia se indexa **origen, destino**. |
| `messages[target, source]` | La matriz de mensajes está **transpuesta** respecto a la anterior. `messages[t, s] == 1` significa "`s` le transmitió a `t`". La transposición es deliberada y coincide con el backend. |
| Simetría del azar | En grafos **no dirigidos** se reutiliza la misma muestra aleatoria para ambas direcciones de una arista. El disparo es simétrico: si `A→B` transmite, `B→A` también. |

Y una cuarta, sobre datos: **pesos repetidos entre el mismo par de nodos se
sobrescriben** en el orden del JSON, igual que al asignar sobre una matriz.

> ⚠️ El motor batched de [`sweep.py`](04-barrido-sweep.md) usa la convención
> **contraria** para los mensajes (`fired[s, source, target]`, sin transponer),
> porque allá se reduce sobre el eje de emisores. Es la diferencia más fácil de
> confundir entre los dos módulos.

---

## 3. Preparación de datos

### `get_adj(data) → np.ndarray (n, n)`

Construye la matriz de adyacencia desde el JSON del editor.

```python
ids = [node["id"] for node in data["nodes"]]
indices = {node_id: index for index, node_id in enumerate(ids)}

matrix = np.zeros((len(ids), len(ids)))
for edge in data["edges"]:
    source, target = indices[edge["source"]], indices[edge["target"]]
    weight = float(edge["weight"])           # ← el string del editor se convierte aquí
    matrix[source, target] = weight
    if not directed:
        matrix[target, source] = weight      # espejo
```

- **El orden de `data["nodes"]` define el orden de filas y columnas.** Todo el
  resto del motor depende de eso.
- Grafo sin nodos → matriz `(0, 0)` válida.
- Self-loops se conservan (`matrix[i, i]`); en no dirigido simplemente reescriben
  la misma celda.
- Un `source`/`target` inexistente lanza `KeyError`, y un peso no numérico
  `ValueError`. **A propósito**: se deja subir la excepción natural de Python en
  vez de inventar validación, igual que el backend. `app.simulate()` la atrapa y
  la muestra con `notify()`.

### `get_values(data) → np.ndarray (n,)`

```python
return np.array([node["value"] for node in data["nodes"]])
```

Mismo orden que `get_adj`. **No fuerza `dtype`** a propósito, para conservar el
comportamiento de `np.array` del original; las APIs de alto nivel convierten a
`float` al final.

---

## 4. El sorteo

### `who_gets_message(matrix, directed, *, rng=None) → (messages, probabilities)`

```python
adjacency_mask = np.ceil(matrix)                              # 1 donde hay arista
probabilities  = _random_array(rng, matrix.shape) * adjacency_mask

if not directed:
    upper = np.triu(probabilities)
    probabilities = upper + upper.T                           # espejo del azar

messages = (matrix > probabilities).astype(int).T             # ← transpuesta
```

**Por qué funciona.** Para un peso `w ∈ (0, 1]`:

- `ceil(w) = 1`, así que `probabilities = u` con `u ~ U(0,1)`.
- La arista dispara si `w > u`, lo que ocurre **con probabilidad exactamente `w`**.
- Donde no hay arista, `w = 0` y `mask = 0` ⟹ `0 > 0` es falso: nunca dispara.
- Con `w = 1` dispara siempre (casi seguramente).

**El `ceil` se conserva literalmente**, con sus consecuencias para pesos fuera de
`(0, 1]`. Por ejemplo `w = 1.5`: `ceil = 2`, así que `probabilities = 2u ~ U(0,2)`
y dispara con probabilidad `1.5/2 = 0.75`, no 1. El comportamiento queda
delegado a la expresión original en vez de recortar en silencio. En la práctica
la UI ya valida el intervalo `(0, 1]` al editar el peso.

**El espejo del no dirigido.** `np.triu` toma el triángulo superior **incluida la
diagonal**, así que `upper + upper.T` copia el azar del triángulo superior al
inferior. Efecto secundario heredado: los elementos de la diagonal (self-loops)
quedan **duplicados** (`2u`), de modo que un self-loop de peso `w` en un grafo no
dirigido dispara con probabilidad `w/2`, no `w`.

**Qué devuelve.** `messages` va **transpuesta** (`[target, source]`) y como `int`;
`probabilities` se queda en orientación `[source, target]`.

**`rng` es opcional.** Si es `None` se usa `np.random.rand`, de modo que
`np.random.seed()` sigue funcionando para reproducir corridas. Si se pasa un
`Generator` moderno, se usa `rng.random(shape)`. Los helpers `_random_array` y
`_random_index` encapsulan esa doble compatibilidad (`Generator.integers` vs.
`RandomState.randint`).

---

## 5. Los algoritmos

Ambos devuelven una lista de tuplas `(nuevo_valor, índice_del_emisor_elegido)`,
una por nodo, en el orden de los nodos.

### `fv(matrix, values, messages, *, rng=None)` — Flip Value

```python
for target_index, received in enumerate(messages):       # messages es [target, source]
    valid_sources = np.where(received != 0)[0]
    if len(valid_sources) == 0:
        new_values.append((values[target_index], None))  # sin mensajes: no cambia
        continue
    source_index = int(valid_sources[_random_index(rng, len(valid_sources))])
    new_values.append((values[source_index], source_index))
```

Cada target que recibió mensajes **elige un emisor uniformemente al azar y copia
su valor**. El segundo elemento de la tupla es el índice del emisor elegido, o
`None` si no hubo elección.

> El backend usaba `NaN` como centinela para "sin emisor". Aquí es `None`:
> preserva la misma decisión y además es JSON-serializable.

`matrix` se recibe pero **no se usa** (`del matrix` en la primera línea): está en
la firma por paridad con el original.

### `amp(matrix, values, messages, agreed=0.5)` — Agreement

```python
for target_index, received in enumerate(messages):
    if np.sum(received) != 0:
        new_values.append((agreed, None))                # adopta el consenso
    else:
        new_values.append((values[target_index], None))  # sin mensajes: no cambia
```

Todo target que recibió **al menos un** mensaje adopta `agreed`. No hay selección
de emisor, así que el índice es siempre `None`. Como el resultado no depende de
*quién* transmitió, AMP no consume aleatoriedad más allá del sorteo.

### Diferencia conceptual

| | FV | AMP |
|---|---|---|
| Valor adoptado | El de un vecino, al azar | Uno fijo (`agreed`) |
| Consenso | **Emergente** — depende de la topología y puede no llegar | **Impuesto** — converge en cuanto todos reciben algo |
| Usa azar en la decisión | Sí | No |
| Valores posibles | Solo los que ya existían en el grafo | Los iniciales + `agreed` |

---

## 6. La traza visual

### `propagation_from_round(node_ids, old_values, messages, updates, algorithm) → list[dict]`

Genera **un registro por arista que disparó**. Es lo que colorea las aristas en
la interfaz.

```python
{"source": id, "target": id, "value_sent": float, "updated_value": bool}
```

Orden: **target-major, luego source-major** (recorre targets y, dentro de cada
uno, sus emisores).

La bandera `updated_value` se calcula distinto según el algoritmo:

- **FV** — `updated_value = (selected_source == source_index)`. Solo el emisor
  sorteado la lleva en `True`; los demás mensajes llegaron pero fueron
  descartados.
- **AMP** — todos los mensajes que llegaron a un mismo target comparten el flag,
  y es `True` solo si el valor **cambió** (`not isclose(nuevo, viejo)`). Si el
  target ya valía `agreed`, todos sus mensajes van en `False`.

Esto es exactamente lo que `app.apply_propagation()` traduce a color:

| `updated_value` | `highlight` | Se ve como |
|---|---|---|
| `True` | `updated` | Naranja animada |
| `False` | `delivered` | Morada animada |
| (sin registro) | `quiet` | Apagada, delgada |

Un algoritmo desconocido lanza `ValueError`. Los ids se conservan tal cual y
todo lo numérico se convierte a tipos Python: el resultado **no contiene nada de
NumPy**, así que es serializable.

### `discrepancy(values) → float`

```python
return float(max(values) - min(values))
```

La métrica central. `0` significa consenso total. Con una colección vacía lanza
`ValueError` — la UI solo habilita simular cuando hay nodos.

---

## 7. Las APIs de alto nivel

### `run_round(...)` — la que usa la aplicación

```python
run_round(matrix_o_grafo, values=None, directed=None,
          algorithm="fv", agreed=0.5, *, node_ids=None, rng=None) → dict
```

**Es la única función de `engine.py` que `app.py` importa.** Acepta dos formas
de llamada:

```python
run_round(grafo_json, algorithm="fv")                    # deriva todo del grafo
run_round(matrix, values, directed, "fv", node_ids=ids)  # con la matriz cruda
```

Mezclar ambas (pasar grafo *y* `values`) lanza `ValueError`. También valida que
la matriz sea cuadrada y que `node_ids` tenga el largo correcto, y **rechaza un
algoritmo inválido antes de consumir aleatoriedad**.

Devuelve:

| Clave | Contenido |
|---|---|
| `values` | Valores finales, `float` nativos. |
| `valuesBefore` | Valores previos. |
| `selectedSources` | Índice del emisor elegido por target (FV) o `None`. |
| `messages` | Matriz entera `[target][source]` como lista de listas. |
| `probabilities` | Muestras uniformes, orientación `[source][target]`. |
| `propagation` | Los registros de la sección anterior. |
| `discrepancy` | `float`, o `None` si el grafo está vacío. |
| `algorithm` | Eco del algoritmo usado. |

Un grafo vacío es válido: produce colecciones vacías y `discrepancy: None`
(`None` en vez de `NaN` para mantener JSON estricto).

### `update_graph_data(data, new_values) → dict`

Copia el grafo con `deepcopy` y reemplaza los valores **sin mutar el argumento**.
Acepta tanto la lista plana de `run_round["values"]` como las tuplas
`(valor, emisor)` de `fv`/`amp`. **Un `NaN` no sobrescribe** el nodo — salvaguarda
heredada del backend.

### `simulate_graph(data, algorithm="fv", agreed=0.5)` (alias: `simulate`)

Contrato exacto del antiguo `POST /simulate`. Siempre **una** ronda:

```python
{"success": True, "graph": {...}, "propagation": [...],
 "algorithm": "fv", "message": "Simulación completada exitosamente"}
```

### `run_simulation(data, ..., rounds=100)`

Varias rondas encadenadas conservando historial:

```python
{"history": [
    {"values": [...], "valuesBefore": None, "propagation": [],
     "discrepancy": 0.7, "roundNum": 0},          # ← estado inicial
    {"values": [...], "valuesBefore": [...], "propagation": [...],
     "discrepancy": 0.4, "roundNum": 1},
    ...
 ],
 "algorithm": "fv"}
```

La topología queda fija; solo evolucionan los valores. `rounds=0` devuelve solo
el estado inicial. Un grafo vacío se rechaza (`ValueError`), a diferencia de
`run_round`.

---

## 8. Ejemplo trabajado

Grafo dirigido de 3 nodos:

```
A(valor 0) ──0.5──▶ B(valor 1) ──1.0──▶ C(valor 0)
```

**Paso 1 · Matrices.**

```
        A    B    C                    A  B  C
   A [  0  0.5   0 ]              A [  0  1  0 ]
matrix B[  0   0   1 ]   ceil →   B [  0  0  1 ]
   C [  0   0    0 ]              C [  0  0  0 ]

values = [0, 1, 0]
```

**Paso 2 · Sorteo.** Supongamos `u[A,B] = 0.30` y `u[B,C] = 0.80`:

- `A→B`: `0.5 > 0.30` ✔ dispara
- `B→C`: `1.0 > 0.80` ✔ dispara

```
                A  B  C                            A  B  C
             A [ F  T  F ]                     A [ F  F  F ]   ← A no recibió
matrix>prob  B [ F  F  T ]   .T →  messages  B [ T  F  F ]   ← B recibió de A
             C [ F  F  F ]                     C [ F  T  F ]   ← C recibió de B
             [source, target]                  [target, source]
```

**Paso 3 · FV.**

| Nodo | Recibió de | Adopta | Resultado |
|---|---|---|---|
| A | nadie | — | `0` (sin cambio) |
| B | A | `values[A] = 0` | `0` |
| C | B | `values[B] = 1` ← **el valor viejo** | `1` |

`values = [0, 0, 1]` · `discrepancy = 1 − 0 = 1`

Aquí se ve la simultaneidad: `C` copió el `1` que `B` tenía **antes** de la ronda,
aunque `B` acabe la ronda valiendo `0`.

**Paso 4 · Propagación.**

```json
[{"source": "A", "target": "B", "value_sent": 0.0, "updated_value": true},
 {"source": "B", "target": "C", "value_sent": 1.0, "updated_value": true}]
```

Ambas aristas se pintan naranja (cada target tenía un solo emisor, así que ambos
fueron el elegido).

**Con AMP y `agreed = 0.5`** el mismo sorteo daría `values = [0, 0.5, 0.5]`,
`discrepancy = 0.5`, y ambos registros con `updated_value: true` (ambos targets
cambiaron).

---

## 9. `simulation/__init__.py`

Solo re-exporta la API pública de `engine` y `sweep` con un `__all__` explícito,
y documenta el contrato general: las APIs de alto nivel devuelven **tipos nativos
serializables**; las funciones numéricas de bajo nivel devuelven `ndarray` para
evitar copias.

**Curiosidad útil:** ni `app.py` ni el Worker usan este `__init__`. Importan
directo (`from simulation.engine import run_round`,
`from simulation.sweep import iter_sweep`). De hecho, de toda la API pública la
aplicación solo consume **dos funciones**:

| Usadas por la app | Solo para pruebas / paridad con el backend |
|---|---|
| `run_round` · `iter_sweep` | `simulate_graph`, `run_simulation`, `run_sweep`, `async_iter_sweep`, `CancellationToken`, y todas las funciones de bajo nivel |

Eso no es código muerto: es la superficie que permite ejercitar el motor desde
Python de escritorio sin navegador.

---

← [Índice](../DOCUMENTACION.md) · Siguiente: [04 · Barrido (sweep)](04-barrido-sweep.md)
