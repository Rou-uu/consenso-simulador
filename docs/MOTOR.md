# El motor: `back/api.py` en el navegador

`python/simulation/` es el backend original (`../Simulador/back/api.py`) sin
FastAPI. Las funciones tienen los mismos nombres y están en el mismo orden, así
que se pueden leer lado a lado. Este documento dice qué hay en cada archivo y
**todo** lo que cambió respecto al backend.

## Qué hay en cada archivo

| Archivo | Funciones | En `api.py` |
|---|---|---|
| `algoritmos.py` | `fv`, `amp`, `fv_batch`, `amp_batch`, `fv_actualizo`, `cambio_el_valor`, `courteous`, `courteous_batch`, `ciclo`, `ciclo_batch`, `ALGORITMOS`, `buscar` | las funciones del algoritmo; `courteous` y `ciclo` son nuevos |
| `ronda.py` | `get_adj`, `get_values`, `who_gets_message_por_arista`, `who_gets_message_por_emisor`, `who_gets_message`, `propagacion`, `run_round`, `update_graph_data`, `simulate_graph` | `POST /simulate` |
| `barrido.py` | `who_gets_message_batch_por_arista`, `who_gets_message_batch_por_emisor`, `who_gets_message_batch`, `round_batch`, `run_one_simulation_naive`, `iter_sweep`, `simulacion_del_sweep` | `POST /simulate/sweep` y `/simulate/sweep/naive` (`simulacion_del_sweep` es nueva) |
| `teoria.py` | `sorteos`, `curva_exacta` | no existe: es nuevo |
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

## Courteous y ciclo

Vienen de *Optimal One-Round Consensus with Five Players* (sección 2), cuyo modelo
es el modo `por_emisor`. Solo aceptan valores 0 y 1, y dan error con cualquier otro.

- **`courteous`**: mayoría entre el valor propio y lo recibido; en un empate decide
  `1 - x_i`.
- **`ciclo`**: el último nodo es el líder `L` y necesita al menos 4 nodos. El ciclo
  `0->1->...->0` es solo una regla de desempate entre regulares, no una topología.

El teorema del paper cubre 5 nodos. Con otra cantidad, `courteous` sigue la misma
regla y `ciclo` es una extensión nuestra, sin garantía de óptimo.

Para reproducir la cota `min{C(p), A(p)}` se usa el modo Broadcast, 1 ronda, un grafo
completo y la peor entrada de cada uno:

| Algoritmo | Valores | Curva | Es la cota |
|---|---|---|---|
| `courteous` | `(0,0,1,1,1)` | `C(p) = q^5 + 6p^2q^3 + 3p^4q` | para `p <= 0.688...` |
| `ciclo` | `(0,0,0,1,0)` | `A(p) = q - 2pq^4` | para `p >= 0.688...` |

En `ciclo`, `A(p)` es su peor caso solo desde `p = 2/3`. Con valores binarios, la
discrepancia media del sweep es la probabilidad de desacuerdo del paper.

## El sweep (`iter_sweep`)

Para cada `p` en `linspace(0, 1, num_p_points)` se ponen todas las aristas en
peso `p`, se corren `sims_per_iter` simulaciones de `rounds` rondas y se acumula
la discrepancia final (`max - min`). Cada iteración es una pasada completa sobre
todos los `p` y emite la curva con media e IC 95 %.

- Si el algoritmo tiene `batch`, las simulaciones corren todas a la vez
  (`round_batch`). Ojo: ahí `fired[s, source, target]` **no** está transpuesta.
- Si no lo tiene, corren una por una con `run_one_simulation_naive`: el mismo
  resultado en promedio, pero de 13 a 40 veces más lento.

**Semilla.** `iter_sweep(..., semilla=s)` siembra el `np.random` global al
empezar **cada bloque** (pasada, p) con `semilla_de_bloque(s, pasada, índice de
p)`. Misma red, algoritmo, parámetros, modo y semilla dan la misma curva. Sin
`semilla` no siembra nada y se comporta como antes (así lo llama el arnés de
equivalencia).

Sembrar por bloque es lo que permite **repetir cualquier simulación del sweep**
sin repetir el sweep: `simulacion_del_sweep` vuelve a sembrar el bloque de esa
simulación y lo corre con las mismas llamadas, en el mismo orden, que
`round_batch` (o que el bucle de `run_one_simulation_naive`), guardando cada
ronda de la fila pedida. Cuesta lo que una pasada en un p, no el sweep entero.
`smoke_ui.py` comprueba que repetir todas las simulaciones de cada p da
exactamente la media que reportó el sweep, con y sin `batch`.

Con `batch` no se sabe de quién copió cada nodo (FV lo decide dentro de su
`batch`); nodo por nodo, sí (`eligio`).

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
| `lider` | `True` si el último nodo es el líder; el editor lo dibuja como cuadrado | todos los nodos son círculos |
| `determinista` | `True` si `batch` no usa azar: habilita la curva teórica exacta | sin curva teórica |
| `esperanza` | `esperanza(vals, fired, params)`: discrepancia esperada por fila para un algoritmo con azar propio (como FV) | sin curva teórica |

Escribe `ronda`, `batch` y `actualizo` como `def` en `algoritmos.py`, no como
`lambda`: la pestaña «Código» los muestra y los copia con `inspect.getsource`.

Para que el arnés lo cubra, agrega su clave a la tupla `ALGORITMOS` de
`scripts/equivalencia.py` y regrábalo (agregar claves no altera las viejas). Un
algoritmo que no acepta cualquier grafo, como `courteous` o `ciclo`, lleva casos
propios en vez de ir en esa tupla.

## La curva teórica (`teoria.py`)

`curva_exacta` da la discrepancia esperada **exacta** tras **una** ronda, sin
simular. En el sweep cada sorteo de entrega sale con probabilidad `p`; con `m`
sorteos independientes hay `2^m` patrones, y uno con `k` éxitos pesa
`p^k (1-p)^(m-k)`. Se corre el algoritmo sobre todos los patrones a la vez (con
su `batch`, que ya recibe `fired[s, source, target]`) y la curva es el polinomio
`sum_k c_k p^k (1-p)^(m-k)`.

Los sorteos son los del sweep: uno por arista en dirigidos, uno por par en no
dirigidos (`a->b` y `b->a` comparten), uno por emisor en broadcast. Para FV, cuyo
`batch` usa azar, `fv_esperanza` da la esperanza exacta sobre ese azar: cada nodo
elige su emisor independiente de los demás, así que `P(max <= t)` es un producto.

Coincide con las fórmulas de los papers donde están demostradas (`smoke_ui.py`
lo comprueba al error de redondeo), y con la simulación en otras redes:

| Algoritmo | Red y entrada | Modo | Curva |
|---|---|---|---|
| FV | 2 nodos, dos arcos (o broadcast) | por arista | `p² + q²` |
| AMP | 2 nodos, dos arcos, `agreed` entre los valores | por arista | `q` |
| FV | 2 nodos, **no dirigido** | por arista | `1`: el sorteo compartido hace que ambos reciban o ninguno |
| Courteous | completa, `(0,0,1,1,1)` | broadcast | `C(p)` |
| Ciclo | completa, `(0,0,0,1,0)` | broadcast | `A(p)` |

No hay curva (y el análisis dice por qué) con más de una ronda, con más de
`2^14` patrones, con lazos, o si el algoritmo no declara `determinista` ni
`esperanza`. El Worker la calcula antes de fijar la semilla; no usa azar, así que
no altera la corrida.

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
| `courteous`, `ciclo` | — | protocolos del paper de cinco jugadores | comparar con su cota óptima |
| `usuario.py` | — | algoritmos pegados en la interfaz, bajo la clave `user_alg` | probar un algoritmo sin tocar el repo |
| `iter_sweep`, `semilla_de_bloque` | el azar es el `np.random` global tal como esté | con `semilla`, cada bloque (pasada, p) se siembra por separado | repetir el sweep y cualquiera de sus simulaciones |
| `simulacion_del_sweep` | — | repite una simulación del sweep ronda por ronda | el desglose por rondas del análisis |
| `teoria.py`, claves `determinista` y `esperanza` | — | discrepancia esperada exacta de una ronda | la columna y la curva teórica del análisis |

Lo que **no** se trajo de `api.py`: el servidor (FastAPI, CORS, `/health`), el
`delay_ms` del stream y el endpoint `/simulate/sweep/naive` como algo aparte
(su bucle es ahora el respaldo del sweep).
