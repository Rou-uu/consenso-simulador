# El motor: `back/api.py` en el navegador

`python/simulation/` es el backend original (`../Simulador/back/api.py`) sin
FastAPI. Las funciones tienen los mismos nombres y están en el mismo orden, así
que se pueden leer lado a lado. Este documento dice qué hay en cada archivo y
**todo** lo que cambió respecto al backend.

## Qué hay en cada archivo

| Archivo | Funciones | En `api.py` |
|---|---|---|
| `algoritmos.py` | `fv`, `amp`, `fv_batch`, `amp_batch`, `fv_actualizo`, `cambio_el_valor`, `ALGORITMOS`, `buscar` | las funciones del algoritmo |
| `ronda.py` | `get_adj`, `get_values`, `who_gets_message_por_arista`, `who_gets_message_por_emisor`, `who_gets_message`, `propagacion`, `run_round`, `update_graph_data`, `simulate_graph` | `POST /simulate` |
| `barrido.py` | `who_gets_message_batch_por_arista`, `who_gets_message_batch_por_emisor`, `who_gets_message_batch`, `round_batch`, `run_one_simulation_naive`, `iter_sweep` | `POST /simulate/sweep` y `/simulate/sweep/naive` |
| `usuario.py` | `instalar` | no existe: es nuevo |

Quién llama a qué:

- **El editor** («Simular») manda el grafo al Worker, que llama a
  `ronda.run_round(graph, algorithm, params)`.
- **El análisis** («Ejecutar sweep») manda el grafo al Worker, que recorre
  `barrido.iter_sweep(...)` y publica cada evento.
- **El hilo principal** solo lee `ALGORITMOS` para armar el selector y los campos
  de parámetros.
- `simulate_graph` y `update_graph_data` no los usa la app: conservan la
  respuesta exacta de `POST /simulate`, y el arnés de equivalencia la verifica.

## Una ronda, paso a paso (`run_round`)

1. `get_adj` arma la matriz: `matrix[source, target]` es el peso de la arista.
2. `who_gets_message` sortea qué aristas transmiten. Una arista de peso `p`
   dispara con probabilidad `p`. Devuelve `messages` **transpuesta**:
   `messages[target, source] == 1` significa que `target` recibió de `source`.
3. La `ronda` del algoritmo recibe `(matrix, values, messages, params)` y
   devuelve, por nodo, `(valor_nuevo, emisor_elegido | None)`.
4. `propagacion` describe cada mensaje enviado; el predicado `actualizo` del
   algoritmo decide si esa arista cuenta como actualización (solo afecta el
   color).

## Modos de paso de mensajes

`who_gets_message` y `who_gets_message_batch` reciben `modo` y eligen el sorteo.
`run_round`, `round_batch`, `run_one_simulation_naive` e `iter_sweep` lo pasan
hasta ahí. `messages` y `fired` conservan su forma, así que los algoritmos no
cambian.

| `modo` | Qué se sortea | Consecuencia |
|---|---|---|
| `por_arista` (por omisión) | un número por arista; en no dirigidos, compartido por `a->b` y `b->a` | es el comportamiento de `api.py` |
| `por_emisor` (broadcast) | un número por emisor, compartido por sus aristas salientes | cada arista conserva su `p`; con pesos iguales llega a todos o a nadie |

En `por_emisor`, una arista no dirigida no comparte sorteo entre sus dos sentidos:
`a->b` y `b->a` pueden ocurrir por separado. Forzar la simetría arrastraría el
sorteo de un nodo al otro. `simulate_graph` conserva la respuesta del endpoint y
siempre usa `por_arista`.

## El sweep (`iter_sweep`)

Para cada `p` en `linspace(0, 1, num_p_points)` se ponen todas las aristas en
peso `p`, se corren `sims_per_iter` simulaciones de `rounds` rondas y se acumula
la discrepancia final (`max - min`). Cada iteración es una pasada completa sobre
todos los `p` y emite la curva con media e IC 95 %.

- Si el algoritmo tiene `batch`, las simulaciones corren todas a la vez
  (`round_batch`). Ojo: ahí `fired[s, source, target]` **no** está transpuesta.
- Si no lo tiene, corren una por una con `run_one_simulation_naive`: el mismo
  resultado en promedio, pero de 13 a 40 veces más lento.

## Agregar un algoritmo

Se edita solo `algoritmos.py`: una función y una entrada en `ALGORITMOS`.

```python
def mi_regla(matrix, values, messages, params):
    new_values = []
    for i, v in enumerate(messages):          # v: lo que recibió el nodo i
        ...
        new_values.append((valor_nuevo, None))
    return new_values

ALGORITMOS["mia"] = {"nombre": "Mi regla", "ronda": mi_regla}
```

Claves opcionales:

| Clave | Para qué | Si falta |
|---|---|---|
| `batch` | acelerar el sweep | el sweep usa el bucle ingenuo |
| `actualizo` | regla de coloreado de aristas | «el valor del target cambió» |
| `parametros` | `{"nombre": ("etiqueta", por_omision)}`; la interfaz arma los campos | sin campos |

Para que el arnés lo cubra, agrega su clave a la tupla `ALGORITMOS` de
`scripts/equivalencia.py` y regrábalo (agregar claves no altera las viejas).

## Todo lo que cambió respecto a `api.py`

Cada fila tiene su comentario `# Cambio respecto a api.py` en el código.

| Dónde | En `api.py` | Aquí | Por qué |
|---|---|---|---|
| `fv` | un nodo sin mensajes devuelve emisor `np.nan` | devuelve `None` | viaja como JSON |
| `amp`, `amp_batch` | `agreed` es un argumento | llega en `params["agreed"]` | un algoritmo con otros parámetros no cambia ninguna firma |
| `buscar` | un algoritmo desconocido cae en FV sin avisar | `ValueError: Algoritmo desconocido` | un error de nombre no debe pasar por FV |
| `propagacion` | la regla de `updated_value` está escrita para FV y AMP dentro del endpoint | cada algoritmo trae su `actualizo` | agregar un algoritmo no toca la ronda |
| `run_round` | solo existe el endpoint | devuelve además valores antes/después, matrices y discrepancia, en tipos de Python | es lo que pinta el editor |
| `update_graph_data` | solo acepta tuplas `(valor, emisor)` | acepta también valores sueltos | `simulate_graph` le pasa la lista plana |
| `iter_sweep` | stream ND-JSON con `asyncio.sleep` entre iteraciones | generador para el Worker, mismos eventos | no hay servidor |
| `iter_sweep` | mínimo 10 simulaciones por `p` | mínimo 1 | heredado del port anterior |
| `iter_sweep` | pedir menos simulaciones que iteraciones corría de más | se recortan las iteraciones | el conteo final nunca excede lo pedido |
| `iter_sweep` | solo algoritmos con `batch` | sin `batch` usa `run_one_simulation_naive` (de `/simulate/sweep/naive`) | un algoritmo nuevo solo necesita su `ronda` |
| `who_gets_message`, `who_gets_message_batch` | solo el sorteo por arista | `modo` elige entre `por_arista` y `por_emisor` (broadcast) | comparar modelos de paso de mensajes |
| `usuario.py` | — | algoritmos pegados en la interfaz, bajo la clave `user_alg` | probar un algoritmo sin tocar el repo |

Lo que **no** se trajo de `api.py`: el servidor (FastAPI, CORS, `/health`), el
`delay_ms` del stream y el endpoint `/simulate/sweep/naive` como algo aparte
(su bucle es ahora el respaldo del sweep).
