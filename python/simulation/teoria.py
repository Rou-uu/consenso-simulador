"""Discrepancia esperada exacta tras una ronda (no existe en api.py: es nuevo).

En el sweep todas las aristas tienen peso ``p`` y cada sorteo independiente de
entrega sale con probabilidad ``p``. Con ``m`` sorteos hay ``2^m`` patrones de
entrega, y uno con ``k`` éxitos tiene probabilidad ``p^k (1-p)^(m-k)``. Si se
conoce la discrepancia esperada de cada patrón, la curva exacta es el polinomio

    E[D](p) = sum_k  c_k p^k (1-p)^(m-k),   c_k = suma sobre los patrones con k éxitos.

Los sorteos son los mismos que usa el sweep (``barrido.who_gets_message_batch``):
uno por arista en dirigidos, uno por par en no dirigidos (``a->b`` y ``b->a``
comparten), uno por emisor en broadcast.

Un algoritmo entra si declara cómo se calcula su discrepancia para un patrón:
``"determinista": True`` (su ``batch`` no usa azar: basta correrlo sobre todos
los patrones a la vez) o ``"esperanza"`` (la esperanza sobre su azar interno,
como FV). Si no, no hay curva: adivinar sería peor que no mostrar nada.

``cota_validez`` es aparte: no depende del algoritmo ni se limita a una ronda.
"""

from math import comb

import numpy as np

from .algoritmos import buscar
from .ronda import get_adj, get_values

#: Máximo de sorteos independientes: 2^14 = 16 384 patrones, todos a la vez.
MAX_SORTEOS = 14


def sorteos(mask: np.ndarray, directed: bool, modo: str) -> list:
    """Los sorteos independientes de una ronda: cada uno, las aristas (s, t) que dispara."""
    n = len(mask)
    if modo == "por_emisor":
        return [[(i, j) for j in range(n) if mask[i, j]] for i in range(n) if mask[i].any()]
    if modo != "por_arista":
        raise ValueError(f"Modo de mensajes desconocido: {modo!r}")
    if directed:
        return [[(i, j)] for i in range(n) for j in range(n) if mask[i, j]]
    return [[(i, j), (j, i)] for i in range(n) for j in range(i + 1, n) if mask[i, j]]


def curva_exacta(graph: dict, algorithm: str, params: dict, modo: str, rounds: int,
                 probabilities) -> tuple:
    """``(valores, "")`` con la discrepancia esperada en cada p, o ``(None, motivo)``."""
    algoritmo = buscar(algorithm)
    if rounds != 1:
        return None, "solo se calcula para 1 ronda"
    esperanza = algoritmo.get("esperanza")
    if esperanza is None and not (algoritmo.get("determinista") and "batch" in algoritmo):
        return None, "el algoritmo no declara cómo calcularla"
    mask = get_adj(graph) > 0
    if np.diag(mask).any():
        return None, "la red tiene lazos"
    grupos = sorteos(mask, graph["directed"], modo)
    m = len(grupos)
    if m > MAX_SORTEOS:
        return None, f"hay 2^{m} patrones de entrega; el cálculo exacto llega a 2^{MAX_SORTEOS}"

    values = get_values(graph).astype(float)
    n = len(values)
    exitos = (np.arange(2**m)[:, None] >> np.arange(m)) & 1  # (S, m): qué sorteos salieron
    fired = np.zeros((2**m, n, n), dtype=bool)  # fired[s, source, target], como en batch
    for g, aristas in enumerate(grupos):
        for i, j in aristas:
            fired[:, i, j] = exitos[:, g] == 1
    vals = np.tile(values, (2**m, 1))
    if esperanza is not None:
        disc = np.asarray(esperanza(vals, fired, params), dtype=float)
    else:
        nuevos = algoritmo["batch"](vals, fired, params)
        disc = nuevos.max(axis=1) - nuevos.min(axis=1)

    coef = np.bincount(exitos.sum(axis=1), weights=disc, minlength=m + 1)
    p = np.asarray(probabilities, dtype=float)
    curva = sum(coef[k] * p**k * (1 - p) ** (m - k) for k in range(m + 1))
    return [float(x) for x in np.broadcast_to(curva, p.shape)], ""


def cota_validez(graph: dict, rounds: int, probabilities) -> tuple:
    """``(valores, "")`` con una cota inferior del desacuerdo en un ciclo, o ``(None, motivo)``.

    Vale para todo algoritmo que solo decide valores que vio (validez), con entradas
    0 y 1. Se toman el bloque más largo de 0 y el más largo de 1: si a sus centros
    no les llega el valor contrario por ningún lado, uno decide 0 y el otro 1. La
    información avanza un brinco por ronda con probabilidad ``p``, así que desde
    ``d`` brincos no llega con probabilidad ``P(Bin(rounds, p) < d)``, y la cota es
    el producto de los cuatro lados.

    El producto es la probabilidad exacta de ese evento con un sorteo por arista
    dirigida. Con sorteos compartidos (red no dirigida, broadcast) los cuatro fallos
    se favorecen entre sí, así que el producto queda por debajo y sigue siendo cota.
    """
    mask = get_adj(graph) > 0
    n = len(mask)
    if n < 3 or np.diag(mask).any() or not (mask == mask.T).all() or not (mask.sum(axis=1) == 2).all():
        return None, "la red no es un ciclo"
    orden, previo = [0], None
    while len(orden) <= n:
        siguiente = next(int(j) for j in np.flatnonzero(mask[orden[-1]]) if j != previo)
        if siguiente == 0:
            break
        previo = orden[-1]
        orden.append(siguiente)
    if len(orden) != n:
        return None, "la red no es un ciclo"
    valores = [float(v) for v in get_values(graph)[orden]]
    if set(valores) != {0.0, 1.0}:
        return None, "la entrada debe tener ceros y unos"

    # Se empieza justo después de un cambio de valor, para no partir un bloque.
    inicio = next(i for i in range(n) if valores[i] != valores[i - 1])
    largo = {0.0: 0, 1.0: 0}
    racha = 0
    for i in range(n):
        v = valores[(inicio + i) % n]
        racha = racha + 1 if i and v == valores[(inicio + i - 1) % n] else 1
        largo[v] = max(largo[v], racha)

    p = np.asarray(probabilities, dtype=float)
    cota = np.ones_like(p)
    for L in largo.values():
        for d in (L // 2 + 1, (L + 1) // 2):  # distancias del centro del bloque a cada borde
            cota = cota * sum(comb(rounds, k) * p**k * (1 - p) ** (rounds - k) for k in range(min(d, rounds + 1)))
    return [float(x) for x in cota], ""
