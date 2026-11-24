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
"""

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
