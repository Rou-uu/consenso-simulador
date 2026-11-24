"""Sweep de discrepancia contra p: el endpoint ``POST /simulate/sweep`` de ``back/api.py``.

Para cada ``p`` en ``[0, 1]`` se reemplazan los pesos de todas las aristas por
``p``, se corren muchas simulaciones y se promedia la discrepancia final
(``max - min`` de los valores). El resultado se refina por iteraciones: cada
una es una pasada completa sobre todos los ``p`` y la curva se va estabilizando.

``iter_sweep`` es lo que llama el Worker. Cada diferencia con el backend está
marcada con ``# Cambio respecto a api.py``.
"""

import numpy as np

from .algoritmos import buscar
from .ronda import get_adj, get_values, who_gets_message


def who_gets_message_batch_por_arista(matrix: np.ndarray, adj: np.ndarray, directed: bool, S: int) -> np.ndarray:
    """
    fired[s, i, j] = la arista i->j disparo en la simulacion s. Forma (S, n, n).
    Portado tal cual de pruebas.ipynb (celda e067565a). Ojo: a diferencia de
    who_gets_message, NO está transpuesta.
    """
    n = matrix.shape[0]
    probs = np.random.rand(S, n, n) * adj
    if not directed:
        upper = np.triu(probs)
        probs = upper + np.transpose(upper, (0, 2, 1))
    return matrix > probs


def who_gets_message_batch_por_emisor(matrix: np.ndarray, adj: np.ndarray, directed: bool, S: int) -> np.ndarray:
    """Broadcast: un sorteo por emisor y simulación. Tampoco está transpuesta."""
    n = matrix.shape[0]
    return matrix > np.random.rand(S, n, 1) * adj


def who_gets_message_batch(matrix: np.ndarray, adj: np.ndarray, directed: bool, S: int,
                           modo: str = "por_arista") -> np.ndarray:
    """Elige el modo de paso de mensajes.

    Cambio respecto a api.py: ahí solo existe el sorteo por arista.
    """
    if modo == "por_arista":
        return who_gets_message_batch_por_arista(matrix, adj, directed, S)
    if modo == "por_emisor":
        return who_gets_message_batch_por_emisor(matrix, adj, directed, S)
    raise ValueError(f"Modo de mensajes desconocido: {modo!r}")


def round_batch(algoritmo, matrix: np.ndarray, values: np.ndarray, directed: bool,
                S: int, times: int = 1, params: dict | None = None,
                modo: str = "por_arista") -> np.ndarray:
    """Corre S simulaciones en paralelo, `times` rondas. Devuelve los vals finales (S, n)."""
    adj = np.ceil(matrix)
    vals = np.tile(values.astype(float), (S, 1))
    for _ in range(times):
        fired = who_gets_message_batch(matrix, adj, directed, S, modo)
        vals = algoritmo(vals, fired, params or {})
    return vals


def run_one_simulation_naive(matrix: np.ndarray, values: np.ndarray, directed: bool,
                             ronda, rounds: int, params: dict,
                             modo: str = "por_arista") -> float:
    """Una simulación completa, nodo por nodo, sin numpy vectorizado sobre S.

    Viene del endpoint experimental ``/simulate/sweep/naive``. Cambio respecto a
    api.py: aquí es el respaldo del sweep para los algoritmos sin ``batch``.
    """
    vals = values.copy()
    for _ in range(rounds):
        messages, _ = who_gets_message(matrix, directed, modo)
        vals = np.array([v[0] for v in ronda(matrix, vals, messages, params)], dtype=float)
    return float(vals.max() - vals.min())


def _entero(name: str, value, minimum: int, maximum: int) -> int:
    """Los límites que en api.py ponía el `Field(ge=..., le=...)` de SweepRequest."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or int(value) != value:
        raise ValueError(f"{name} debe ser entero")
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} debe estar entre {minimum} y {maximum}")
    return int(value)


def _reparto(total_simulations_per_p: int, iterations: int) -> tuple[int, int]:
    """``(simulaciones por pasada, pasadas)``.

    Cambio respecto a api.py: ahí pedir menos simulaciones que iteraciones
    corría de más (10 con 50 iteraciones corrían 50). Aquí se recortan las
    iteraciones para no pasarse de lo pedido.
    """
    sims_per_iter = max(1, total_simulations_per_p // iterations)
    return sims_per_iter, min(iterations, total_simulations_per_p // sims_per_iter)


def semilla_de_bloque(semilla: int, iteracion: int, indice_p: int) -> list:
    """La semilla del bloque (pasada, p) de un sweep con semilla.

    Cambio respecto a api.py: no existe ahí. Sembrar cada bloque por separado
    permite repetir cualquier simulación del sweep corriendo solo su bloque
    (``simulacion_del_sweep``), sin repetir el sweep entero.
    """
    return [int(semilla), int(iteracion), int(indice_p)]


def iter_sweep(graph: dict, algorithm: str = "fv", params: dict | None = None, *,
               num_p_points: int = 201, total_simulations_per_p: int = 5000,
               iterations: int = 50, rounds: int = 1, modo: str = "por_arista",
               semilla: int | None = None):
    """Genera los eventos del sweep: ``start``, una ``iteration`` por pasada y,
    si algo falla a medio camino, ``error``.

    Cambio respecto a api.py: es un generador para el Worker en vez de un
    stream ND-JSON de FastAPI; los eventos tienen la misma forma.

    Cambio respecto a api.py: con ``semilla``, cada bloque (pasada, p) se
    siembra con ``semilla_de_bloque``; sin ella usa el ``np.random`` global tal
    como esté, igual que antes.
    """
    algoritmo = buscar(algorithm)
    params = params or {}
    # Cambio respecto a api.py: el mínimo de simulaciones es 1, no 10.
    num_p_points = _entero("num_p_points", num_p_points, 2, 1001)
    total_simulations_per_p = _entero("total_simulations_per_p", total_simulations_per_p, 1, 50_000)
    iterations = _entero("iterations", iterations, 1, 1000)
    rounds = _entero("rounds", rounds, 1, 1000)

    base_matrix = get_adj(graph)
    values = get_values(graph)
    if len(values) == 0:
        raise ValueError("El grafo no tiene nodos")

    edge_mask = base_matrix > 0
    directed = graph["directed"]
    probabilities = np.linspace(0, 1, num_p_points)
    sims_per_iter, iterations = _reparto(total_simulations_per_p, iterations)
    n_p = len(probabilities)

    yield {
        "type": "start",
        "numPPoints": n_p,
        "iterations": iterations,
        "simsPerIteration": sims_per_iter,
        "algorithm": algorithm,
    }

    cum_sum = np.zeros(n_p)
    cum_sumsq = np.zeros(n_p)
    n_done = 0
    try:
        for it in range(iterations):
            for i, p in enumerate(probabilities):
                if semilla is not None:
                    np.random.seed(semilla_de_bloque(semilla, it, i))
                matrix = np.where(edge_mask, p, 0.0)
                if "batch" in algoritmo:
                    vals = round_batch(algoritmo["batch"], matrix, values, directed,
                                       sims_per_iter, times=rounds, params=params, modo=modo)
                    disc = vals.max(axis=1) - vals.min(axis=1)
                else:
                    disc = np.array([
                        run_one_simulation_naive(matrix, values, directed,
                                                 algoritmo["ronda"], rounds, params, modo)
                        for _ in range(sims_per_iter)
                    ])
                cum_sum[i] += disc.sum()
                cum_sumsq[i] += (disc ** 2).sum()
            n_done += sims_per_iter

            mean = cum_sum / n_done
            if n_done > 1:
                var = np.maximum((cum_sumsq - n_done * mean ** 2) / (n_done - 1), 0.0)
            else:
                var = np.zeros(n_p)
            se = np.sqrt(var) / (n_done ** 0.5)
            ci_low = mean - 1.96 * se
            ci_high = mean + 1.96 * se

            yield {
                "type": "iteration",
                "iteration": it,
                "totalIterations": iterations,
                "simulationsPerP": n_done,
                "done": it == iterations - 1,
                "points": [
                    {
                        "p": float(probabilities[i]),
                        "mean": float(mean[i]),
                        "ciLow": float(ci_low[i]),
                        "ciHigh": float(ci_high[i]),
                    }
                    for i in range(n_p)
                ],
            }
    except Exception as e:
        yield {"type": "error", "message": str(e)}


def simulacion_del_sweep(graph: dict, algorithm: str = "fv", params: dict | None = None, *,
                         num_p_points: int, total_simulations_per_p: int, iterations: int,
                         rounds: int, modo: str = "por_arista", semilla: int,
                         indice_p: int, simulacion: int) -> dict:
    """La simulación número ``simulacion`` (desde 0) que corrió ``iter_sweep`` con
    ``semilla`` en el p número ``indice_p``, ronda por ronda.

    Cambio respecto a api.py: no existe ahí. Vuelve a sembrar su bloque con
    ``semilla_de_bloque`` y lo repite tal cual (las mismas llamadas, en el mismo
    orden, que ``round_batch`` o el bucle de ``run_one_simulation_naive``),
    guardando cada ronda de esa simulación.

    ``valores[r]`` son los valores tras la ronda ``r`` (``valores[0]``, los
    iniciales) y ``discrepancias[r]`` su ``max - min``. ``rondas[r - 1]`` dice, por
    nodo, de quién recibió (``recibio``) y, si el algoritmo corre nodo por nodo y
    elige emisor, de quién copió (``eligio``); con ``batch`` eso no se conoce.
    """
    algoritmo = buscar(algorithm)
    params = params or {}
    num_p_points = _entero("num_p_points", num_p_points, 2, 1001)
    total_simulations_per_p = _entero("total_simulations_per_p", total_simulations_per_p, 1, 50_000)
    iterations = _entero("iterations", iterations, 1, 1000)
    rounds = _entero("rounds", rounds, 1, 1000)
    sims_per_iter, iterations = _reparto(total_simulations_per_p, iterations)
    if not 0 <= indice_p < num_p_points:
        raise ValueError(f"indice_p debe estar entre 0 y {num_p_points - 1}")
    if not 0 <= simulacion < sims_per_iter * iterations:
        raise ValueError(f"simulacion debe estar entre 0 y {sims_per_iter * iterations - 1}")
    iteracion, fila = divmod(simulacion, sims_per_iter)

    base_matrix = get_adj(graph)
    values = get_values(graph)
    directed = graph["directed"]
    n = len(values)
    p = np.linspace(0, 1, num_p_points)[indice_p]
    matrix = np.where(base_matrix > 0, p, 0.0)
    np.random.seed(semilla_de_bloque(semilla, iteracion, indice_p))

    valores = [values.astype(float)]
    rondas = []
    if "batch" in algoritmo:
        # Lo mismo que round_batch: todo el bloque, porque el azar se reparte entre sus filas.
        adj = np.ceil(matrix)
        vals = np.tile(values.astype(float), (sims_per_iter, 1))
        for _ in range(rounds):
            fired = who_gets_message_batch(matrix, adj, directed, sims_per_iter, modo)
            vals = algoritmo["batch"](vals, fired, params)
            # fired[s, source, target]: lo que recibió el nodo i es la columna i.
            rondas.append({"recibio": [np.flatnonzero(fired[fila][:, i]).tolist() for i in range(n)],
                           "eligio": [None] * n})
            valores.append(vals[fila].copy())
    else:
        # Lo mismo que el bucle de iter_sweep: las anteriores del bloque consumen su azar.
        for _ in range(fila):
            run_one_simulation_naive(matrix, values, directed, algoritmo["ronda"], rounds, params, modo)
        vals = values.copy()
        for _ in range(rounds):
            messages, _ = who_gets_message(matrix, directed, modo)
            nuevos = algoritmo["ronda"](matrix, vals, messages, params)
            # messages viene transpuesta: la fila i es lo que recibió el nodo i.
            rondas.append({"recibio": [np.flatnonzero(f).tolist() for f in messages],
                           "eligio": [None if e is None else int(e) for _, e in nuevos]})
            vals = np.array([v for v, _ in nuevos], dtype=float)
            valores.append(vals)

    valores = [[float(x) for x in v] for v in valores]
    return {
        "p": float(p),
        "iteracion": iteracion,
        "valores": valores,
        "discrepancias": [max(v) - min(v) if v else 0.0 for v in valores],
        "rondas": rondas,
    }
