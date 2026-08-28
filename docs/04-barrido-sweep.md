← [Índice](../DOCUMENTACION.md) · Anterior: [03 · Motor de simulación](03-motor-simulacion.md)

# 04 · Barrido / sweep (`simulation/sweep.py`)

522 líneas. Port del endpoint `/simulate/sweep` del backend. Responde la pregunta
del panel **Análisis de Convergencia**:

> Si todas las aristas tuvieran la misma probabilidad `p`, ¿cuánta discrepancia
> queda al final, en promedio, para cada `p` entre 0 y 1?

Corre **dentro del Web Worker**, nunca en el hilo principal.

---

## 1. La idea

Para cada valor de `p` en `linspace(0, 1, N)`:

1. Se toma la **topología** del grafo y se reemplazan **todos** los pesos
   positivos por ese mismo `p`.
2. Se corren `S` simulaciones independientes **en paralelo** (vectorizadas).
3. Se mide la discrepancia final de cada una y se acumulan suma y suma de
   cuadrados.

El resultado es una curva de discrepancia media vs. `p`, con banda de confianza
al 95 %.

> **Los pesos originales se descartan.** El sweep usa la topología (qué aristas
> existen), no sus valores. `edge_mask = base_matrix > 0` y después
> `matrix = np.where(edge_mask, p, 0.0)`. Es exactamente el punto del análisis:
> aislar el efecto de `p`.

### Refinamiento progresivo

En vez de calcular `S` simulaciones de un tirón por cada `p`, el trabajo se parte
en `iterations` pasadas. **Cada pasada agrega simulaciones a todos los `p` a la
vez** y publica la curva completa acumulada. Visualmente: la curva aparece
enseguida, ruidosa, y la banda de confianza se va estrechando.

```
simulaciones_por_iteración = max(1, total_simulaciones_por_p // iterations)
```

Con los valores por defecto (5000 sims, 50 iteraciones): 100 simulaciones por
`p` en cada pasada, 50 pasadas.

---

## 2. De un grafo a un tensor

La diferencia con [`engine.py`](03-motor-simulacion.md) es que aquí **todo lleva
un eje extra de simulación**:

| | `engine.py` | `sweep.py` |
|---|---|---|
| Valores | `(n,)` | `(S, n)` — una fila por réplica |
| Sorteo | `(n, n)` | `(S, n, n)` |
| Mensajes | `messages[target, source]` **transpuesta** | `fired[s, source, target]` **sin transponer** |
| Decisión | Bucle Python sobre nodos | `np.where` / `argmax` sin bucles |

La convención de mensajes **es la contraria** a propósito: los algoritmos batched
reducen sobre el eje de emisores (`axis=1`), y con esa orientación la reducción
sale directa.

---

## 3. Cancelación cooperativa

### `SweepCancelled(RuntimeError)`

Excepción interna para salir limpio de un batch a medias.

### `CancellationToken`

```python
token = CancellationToken()
token.cancelled   # → False
token.cancel()    # idempotente
```

### `is_cancelled(cancel) → bool`

Acepta cualquiera de estas cuatro formas, para poder cancelar desde contextos
distintos:

| Se le pasa | Cómo lo lee |
|---|---|
| `None` | Nunca cancela. |
| Un callable | `bool(cancel())` |
| Un `Mapping` | `cancel.get("cancelled", False)` |
| Cualquier objeto (o proxy de Pyodide) | `getattr(cancel, "cancelled", False)` |

Si lo obtenido es a su vez callable, se invoca.

`_check_cancelled(cancel)` lanza `SweepCancelled` si corresponde, y se llama en
**tres niveles**: antes de cada iteración, antes de cada punto de `p` y antes de
cada ronda dentro de `round_batch`.

> **En la práctica la UI no usa esto.** El `for` que consume el generador bloquea
> el Worker, así que el Worker no puede procesar un mensaje de cancelación
> mientras calcula. `app.stop_sweep()` llama a `WORKER.terminate()`, que es
> inmediato y no requiere cooperación. El token existe para consumidores que
> compartan una bandera consultable (por ejemplo el controlador `async`).

---

## 4. Las primitivas vectorizadas

### `who_gets_message_batch(matrix, adjacency_mask, directed, simulations, *, rng=None) → bool (S,n,n)`

```python
probabilities = _random_array(rng, (simulations, n_nodes, n_nodes))
probabilities *= adjacency_mask                       # broadcast de (n,n) sobre (S,n,n)
if not directed:
    upper = np.triu(probabilities)                    # actúa sobre los 2 últimos ejes
    probabilities = upper + np.transpose(upper, (0, 2, 1))
return matrix > probabilities                         # NO se transpone
```

Misma matemática que `engine.who_gets_message`, con dos diferencias:

- `adjacency_mask` (= `ceil(matrix)`) **se recibe ya calculado**, para reutilizarlo
  en todas las rondas del mismo `p` en vez de recalcularlo.
- El resultado es **booleano** y en orientación `[s, source, target]`.

Para el no dirigido, `np.triu` sobre un array 3D opera sobre los dos últimos
ejes, y `np.transpose(upper, (0,2,1))` es la transposición por réplica.

### `amp_batch(values, fired, agreed=0.5, *, rng=None) → (S, n)`

```python
received = fired.any(axis=1)                # axis=1 = eje de emisores → (S, n)
return np.where(received, agreed, values)
```

Todo el algoritmo en dos líneas. `rng` se acepta y se descarta (`del rng`): existe
solo para compartir firma con FV y que `round_batch` pueda llamarlas igual.

### `fv_batch(values, fired, agreed=0.5, *, rng=None) → (S, n)`

El truco interesante del módulo:

```python
received = fired.any(axis=1)                      # (S, n) ¿recibió algo?
keys     = fired * _random_array(rng, fired.shape)  # (S,n,n) llave uniforme por arista
chosen   = keys.argmax(axis=1)                    # (S, n) índice del emisor ganador
adopted  = np.take_along_axis(values, chosen, axis=1)
return np.where(received, adopted, values)
```

**Por qué `argmax` selecciona uniformemente.** A cada arista que disparó se le
asigna una llave `U(0,1)` independiente; las que no dispararon quedan en 0. El
máximo de `k` uniformes independientes es igualmente probable en cualquiera de
las `k` posiciones, así que `argmax` elige un emisor **con probabilidad uniforme**
— reproduciendo el `random.choice` del motor individual sin un solo bucle Python.

El caso "no recibió nada" también está cubierto: `argmax` sobre puros ceros
devuelve `0`, pero `np.where(received, ...)` descarta ese valor y conserva el
previo.

`agreed` se ignora (`del agreed`), por simetría con AMP.

```python
BATCH_ALGORITHMS = {"fv": fv_batch, "amp": amp_batch}
```

### `round_batch(algorithm, matrix, values, directed, simulations, times=1, agreed=0.5, *, rng=None, cancel=None) → (S, n)`

Corre `times` rondas sobre `simulations` réplicas paralelas.

```python
adjacency_mask  = np.ceil(matrix)
batched_values  = np.tile(values.astype(float), (simulations, 1))   # (S, n)
for _ in range(times):
    _check_cancelled(cancel)
    fired = who_gets_message_batch(matrix, adjacency_mask, directed, simulations, rng=rng)
    batched_values = algorithm_fn(batched_values, fired, agreed, rng=rng)
return batched_values
```

- `algorithm` acepta el nombre (`"fv"`/`"amp"`) **o** directamente la función batched.
- Valida `simulations ≥ 1`, `times ≥ 1` y que la matriz sea cuadrada y coincida
  con `values`; cero simulaciones se rechaza en vez de producir estadísticas
  ambiguas.
- `np.tile` replica el vector inicial en `S` filas: cada fila evoluciona
  independientemente porque todo el sorteo lleva su propio eje.

**Coste de memoria.** El tensor `(S, n, n)` de flotantes se materializa dos veces
por ronda (las uniformes y el producto). Con `S = 100` y `n = 50`: 100·50·50·8 B ≈
2 MB por tensor. Con `S = 5000` (todas las simulaciones en una sola iteración) el
mismo grafo pediría ~100 MB. Ese es el otro motivo, además del refinamiento
visual, para partir el trabajo en iteraciones.

---

## 5. La estadística

### `SweepAccumulator`

Guarda sumas por bin de `p`, no las muestras. Memoria O(N) sin importar cuántas
simulaciones se acumulen.

```python
@dataclass
class SweepAccumulator:
    probabilities: np.ndarray        # los N valores de p
    cumulative_sum: np.ndarray       # Σ x   por bin
    cumulative_sumsq: np.ndarray     # Σ x²  por bin
    simulations_per_p: int = 0       # n acumulado, común a todos los bins
```

| Método | Detalle |
|---|---|
| `add(probability_index, discrepancies)` | Acumula `sum` y `sum²` de un batch en un bin. |
| `finish_iteration(simulations)` | Avanza el contador **una sola vez por pasada completa**, cuando ya se recorrieron todos los `p`. |
| `points()` | Devuelve la curva. |

El contador es **compartido** porque cada iteración agrega exactamente el mismo
número de simulaciones a cada bin. Eso mantiene la curva estadísticamente
homogénea: nunca hay un `p` con más muestras que otro.

### `points() → list[{p, mean, ciLow, ciHigh}]`

```python
mean = cumulative_sum / n
variance = max((cumulative_sumsq - n * mean²) / (n - 1), 0)   # varianza muestral
standard_error = sqrt(variance) / sqrt(n)
ci_low, ci_high = mean ∓ 1.96 * standard_error                 # normal al 95 %
```

- Fórmula de varianza **por sumas acumuladas**: permite refinar sin guardar las
  muestras. El `np.maximum(..., 0.0)` protege contra la cancelación catastrófica
  que puede dar una varianza negativa diminuta en punto flotante.
- Con `n = 1` la varianza es 0 (no hay grados de libertad) y el intervalo colapsa
  a la media.
- **Los límites no se recortan**, tal como el backend: cerca de `p = 0` o `p = 1`
  el `ciLow` puede salir negativo aunque la discrepancia no pueda serlo.
- Llamar `points()` sin simulaciones acumuladas lanza `ValueError`.

---

## 6. Validación

### `_validated_int(name, value, minimum, maximum) → int`

Replica los `Field(...)` del `SweepRequest` original. Rechaza `bool` (que en
Python es `int`) y valores no enteros (`3.5` falla; `3.0` pasa).

| Parámetro | Rango | Default |
|---|---|---|
| `num_p_points` | 2 – 1001 | 201 |
| `total_simulations_per_p` | 10 – 50 000 | 5000 |
| `iterations` | 1 – 1000 | 50 |
| `rounds` | 1 – 1000 | 1 |
| `delay_ms` (solo async) | 0 – 2000 | 100 |

Los mismos límites están en los `min`/`max` de los `<input>` de `index.html`,
pero **el HTML no los impone**: `app.positive_int()` solo aplica el mínimo. Un
valor por encima del máximo llega hasta `_prepare_sweep`, que lanza `ValueError`
antes del primer `yield`; el `try/catch` del Worker lo convierte en
`{type:"error"}` y la UI lo muestra en `#sweep-status`.

### `_prepare_sweep(...)`

Valida y precalcula lo que se reutiliza en todo el barrido:

```python
edge_mask = base_matrix > 0                          # topología, sin pesos
probabilities = np.linspace(0, 1, num_p_points)      # los valores de p
simulations_per_iteration = max(1, total // iterations)
```

También rechaza un grafo sin nodos (`ValueError`).

**Nota sobre la división entera.** Si `total` no es divisible entre `iterations`,
el conteo final es **menor** que el solicitado (5000 // 300 = 16 → 16·300 = 4800).
Y si hay más iteraciones que simulaciones, el `max(1, ...)` hace que sea **mayor**
(10 sims / 50 iteraciones → 1·50 = 50). Ese caso límite es parte del
comportamiento observable del backend y se conserva.

---

## 7. Las tres APIs de salida

### `iter_sweep(...)` — la que usa el Worker

```python
iter_sweep(graph, algorithm="fv", agreed=0.5, *,
           num_p_points=201, total_simulations_per_p=5000,
           iterations=50, rounds=1, rng=None, cancel=None) → Iterator[dict]
```

Generador de eventos. Estructura:

```python
yield {"type": "start", "numPPoints", "iterations", "simsPerIteration", "algorithm"}

for iteración in range(total_iterations):
    for índice, p in enumerate(probabilities):
        matrix = np.where(edge_mask, p, 0.0)
        final_values = round_batch(algorithm, matrix, values, directed,
                                   sims_por_iteración, times=rounds, agreed=agreed)
        discrepancias = final_values.max(axis=1) - final_values.min(axis=1)   # (S,)
        accumulator.add(índice, discrepancias)
    accumulator.finish_iteration(sims_por_iteración)
    yield {"type": "iteration", "iteration", "totalIterations",
           "simulationsPerP", "done", "points": [...]}
```

**Contrato de eventos:**

| `type` | Cuándo | Campos |
|---|---|---|
| `start` | Siempre, primero. Equivale al primer registro ND-JSON del backend. | `numPPoints`, `iterations`, `simsPerIteration`, `algorithm` |
| `iteration` | Una por pasada completa. | `iteration` (0-based), `totalIterations`, `simulationsPerP`, `done` (`True` en la última), `points` |
| `cancelled` | Terminación cooperativa limpia. **No** emite una curva parcial de una iteración inconclusa. | `iteration`, `totalIterations`, `simulationsPerP`, `done: True` |
| `error` | Fallo **posterior** a `start`. | `message` |

Los errores de validación ocurren **antes** de `start` y por lo tanto se propagan
como excepción normal, no como evento; los errores durante el cálculo viajan
como evento `error` y no derriban al consumidor del generador (paridad con el
stream del backend).

### `async_iter_sweep(..., delay_ms=100)`

Mismo contrato, versión `async`. Delega en `iter_sweep` y añade
`await asyncio.sleep(delay_ms/1000)` **después** de cada evento `iteration`, para
dejar que la UI pinte o que se actualice una bandera de cancelación. Pensado para
un controlador que corra en el hilo principal en lugar de un Worker. **La
aplicación actual no lo usa.**

### `run_sweep(graph, algorithm, agreed, *, on_event=None, **options) → dict`

Consume el generador completo y devuelve el **último** evento. Con
`on_event=post_message` publica el progreso sin acumular historia; sin callback
sirve como llamada síncrona simple que retorna la curva final (o el evento de
error/cancelación). **Tampoco la usa la aplicación** — el Worker itera
directamente.

---

## 8. Interpretar la gráfica

| Zona | Qué significa |
|---|---|
| `p → 0` | Casi ningún mensaje llega. La discrepancia final ≈ la inicial. |
| `p → 1` | Todas las aristas transmiten siempre. El valor depende del algoritmo y de la topología. |
| Caída brusca | Umbral de conectividad efectiva: a partir de ese `p` la red suele alcanzar consenso. |
| Banda ancha | Pocas simulaciones acumuladas todavía, o alta varianza real (resultados bimodales: unas corridas convergen y otras no). |

Subir **`rounds`** cambia la pregunta: en vez de "¿cuánta discrepancia queda tras
una ronda?", es "¿tras `rounds` rondas?". Con FV y varias rondas la curva suele
desplazarse hacia el consenso.

---

← [Índice](../DOCUMENTACION.md) · Siguiente: [05 · Controlador](05-controlador-app.md)
