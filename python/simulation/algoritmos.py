"""Algoritmos de consenso. **Aquí, y solo aquí, se agrega un algoritmo.**

Las funciones vienen de ``back/api.py`` (y este de ``pruebas.ipynb``). Cada
diferencia con el backend está marcada con ``# Cambio respecto a api.py``.

Un algoritmo es una entrada de ``ALGORITMOS`` con estas claves:

``nombre``      (obligatoria) lo que se ve en el selector.
``ronda``       (obligatoria) ``ronda(matrix, values, messages, params)``.
                Una ronda nodo por nodo. ``messages[i]`` es lo que recibió el
                nodo ``i``: ``messages[target, source] == 1`` (viene TRANSPUESTA).
                Devuelve una lista de ``(valor_nuevo, emisor_elegido | None)``.
``batch``       (opcional) ``batch(vals, fired, params)``. La misma regla para
                ``S`` simulaciones a la vez, solo para acelerar el sweep. Ojo:
                aquí ``fired[s, source, target]`` NO está transpuesta. Sin
                ``batch`` el sweep corre ``ronda`` simulación por simulación:
                da lo mismo, pero mucho más lento.
``actualizo``   (opcional) ``actualizo(nuevos, valores, target, source)``.
                ¿La arista ``source -> target`` cuenta como actualización? Solo
                decide el color de la arista, no la matemática. Por omisión:
                «el valor del target cambió».
``lider``       (opcional) ``True`` si el último nodo es el líder. Solo cambia
                cómo se dibuja: la interfaz lo pinta como cuadrado.
``parametros``  (opcional) ``{nombre: (etiqueta, valor_por_omision)}``. La
                interfaz arma un campo por parámetro y los manda en ``params``.
"""

import numpy as np


def fv(matrix: np.ndarray, values: np.ndarray, messages: np.ndarray, params: dict) -> list:
    """
    Algoritmo flip value: cada nodo que recibió mensajes copia el valor de un
    emisor elegido al azar. Lógica exacta de pruebas.ipynb
    """
    new_values = []
    for i, v in enumerate(messages):
        valid_ind = np.where(v != 0)[0]

        if len(valid_ind) == 0:
            # Cambio respecto a api.py: None en vez de np.nan, para que viaje como JSON.
            new_values.append((values[i], None))
            continue

        ind = valid_ind[np.random.randint(0, len(valid_ind))]
        new_values.append((values[ind], int(ind)))

    return new_values


def amp(matrix: np.ndarray, values: np.ndarray, messages: np.ndarray, params: dict) -> list:
    """
    Algoritmo AMP: si el nodo recibe al menos un mensaje, adopta el valor `agreed`.
    En caso contrario, mantiene su valor actual.
    """
    # Cambio respecto a api.py: `agreed` llega dentro de `params`, no como argumento.
    agreed = params.get("agreed", 0.5)
    new_values = []
    for i, v in enumerate(messages):
        if np.sum(v) != 0:
            new_values.append((agreed, None))
        else:
            new_values.append((values[i], None))

    return new_values


def fv_batch(vals: np.ndarray, fired: np.ndarray, params: dict) -> np.ndarray:
    recibio = fired.any(axis=1)
    keys = fired * np.random.rand(*fired.shape)
    chosen = keys.argmax(axis=1)
    adopted = np.take_along_axis(vals, chosen, axis=1)
    return np.where(recibio, adopted, vals)


def amp_batch(vals: np.ndarray, fired: np.ndarray, params: dict) -> np.ndarray:
    recibio = fired.any(axis=1)
    return np.where(recibio, params.get("agreed", 0.5), vals)


def fv_actualizo(nuevos: list, valores: np.ndarray, target: int, source: int) -> bool:
    """En FV solo cuenta el emisor seleccionado."""
    return nuevos[target][1] == source


def cambio_el_valor(nuevos: list, valores: np.ndarray, target: int, source: int) -> bool:
    """Por omisión (es la regla de AMP en api.py): cuenta si el valor del target cambió."""
    return not np.isclose(nuevos[target][0], valores[target])


# Cambio respecto a api.py: courteous y ciclo no existen ahí. Vienen de «Optimal
# One-Round Consensus with Five Players» (sección 2). Ver docs/MOTOR.md.


def _binarios(values: np.ndarray) -> None:
    """Courteous y ciclo solo están definidos para entradas 0 y 1."""
    if not np.isin(values, (0, 1)).all():
        raise ValueError("Este algoritmo solo acepta valores 0 y 1")


def courteous(matrix: np.ndarray, values: np.ndarray, messages: np.ndarray, params: dict) -> list:
    """Mayoría entre el valor propio y lo recibido; en un empate decide 1 - x_i."""
    _binarios(values)
    new_values = []
    for i, v in enumerate(messages):
        vistos = [values[i]] + [values[j] for j in np.where(v != 0)[0]]
        unos = sum(vistos)
        if 2 * unos == len(vistos):
            new_values.append((1.0 - values[i], None))
        else:
            new_values.append((float(2 * unos > len(vistos)), None))
    return new_values


def courteous_batch(vals: np.ndarray, fired: np.ndarray, params: dict) -> np.ndarray:
    _binarios(vals)
    # fired[s, source, target]: lo recibido se suma sobre source (eje 1).
    unos = vals + (fired * vals[:, :, None]).sum(axis=1)
    vistos = 1 + fired.sum(axis=1)
    return np.where(2 * unos == vistos, 1.0 - vals, (2 * unos > vistos).astype(float))


def _ciclo_regulares(n: int) -> int:
    if n < 4:
        raise ValueError("El algoritmo de ciclo necesita al menos 4 nodos")
    return n - 1


def _regular(i: int, m: int, x_i: float, x_L: float, de_L: bool, recibidos: dict) -> float:
    """Decisión de un regular. `recibidos` son los {j: x_j} de otros regulares."""
    R = [x_i] + list(recibidos.values())
    unanime = len(set(R)) == 1
    if de_L:
        return R[0] if len(R) >= 3 and unanime else x_L
    if unanime:
        return R[0]
    if len(R) == 2:
        j, x_j = next(iter(recibidos.items()))
        return x_i if j == (i + 1) % m else x_j
    unos = sum(R)
    if 2 * unos == len(R):
        return x_i
    return float(2 * unos < len(R))


def ciclo(matrix: np.ndarray, values: np.ndarray, messages: np.ndarray, params: dict) -> list:
    """El último nodo es el líder L; los demás desempatan con el ciclo 0->1->...->0.

    El ciclo no es una topología: los mensajes siguen las aristas del grafo.
    """
    m = _ciclo_regulares(len(values))
    _binarios(values)
    L = m
    x_L = float(values[L])
    de_regulares = [float(values[j]) for j in np.where(messages[L] != 0)[0]]
    decide_L = de_regulares[0] if len(de_regulares) >= 2 and len(set(de_regulares)) == 1 else x_L
    new_values = []
    for i in range(m):
        recibidos = {j: float(values[j]) for j in range(m) if j != i and messages[i][j] != 0}
        new_values.append((_regular(i, m, float(values[i]), x_L, messages[i][L] != 0, recibidos), None))
    new_values.append((decide_L, None))
    return new_values


def ciclo_batch(vals: np.ndarray, fired: np.ndarray, params: dict) -> np.ndarray:
    m = _ciclo_regulares(vals.shape[1])
    _binarios(vals)
    L = m
    x_L = vals[:, L]
    salida = vals.copy()
    llegan_a_L = fired[:, :m, L].sum(axis=1)
    unos_L = (fired[:, :m, L] * vals[:, :m]).sum(axis=1)
    iguales = (llegan_a_L >= 2) & ((unos_L == 0) | (unos_L == llegan_a_L))
    salida[:, L] = np.where(iguales, (unos_L > 0).astype(float), x_L)
    for i in range(m):
        otros = [j for j in range(m) if j != i]
        x_i = vals[:, i]
        llega = fired[:, otros, i]
        tam = 1 + llega.sum(axis=1)
        unos = x_i + (llega * vals[:, otros]).sum(axis=1)
        unanime = (unos == 0) | (unos == tam)
        valor_unanime = (unos > 0).astype(float)
        # Con |R| = 2 y sin unanimidad, el valor recibido es 1 - x_i.
        desempate = np.where(fired[:, (i + 1) % m, i], x_i, 1.0 - x_i)
        sin_L = np.select(
            [unanime, tam == 2, 2 * unos == tam],
            [valor_unanime, desempate, x_i],
            (2 * unos < tam).astype(float),
        )
        con_L = np.where((tam >= 3) & unanime, valor_unanime, x_L)
        salida[:, i] = np.where(fired[:, L, i], con_L, sin_L)
    return salida


ALGORITMOS = {
    "fv": {
        "nombre": "Flip Value (FV)",
        "ronda": fv,
        "batch": fv_batch,
        "actualizo": fv_actualizo,
    },
    "amp": {
        "nombre": "Agreement (AMP)",
        "ronda": amp,
        "batch": amp_batch,
        "parametros": {"agreed": ("Valor de consenso (<code>agreed</code>)", 0.5)},
    },
    "courteous": {
        "nombre": "Courteous",
        "ronda": courteous,
        "batch": courteous_batch,
    },
    "ciclo": {
        "nombre": "Ciclo",
        "ronda": ciclo,
        "batch": ciclo_batch,
        "lider": True,
    },
}


def buscar(algorithm: str) -> dict:
    """La entrada de ``algorithm``, o un error que lo dice."""
    # Cambio respecto a api.py: ahí un nombre desconocido caía en silencio en FV.
    if algorithm not in ALGORITMOS:
        raise ValueError(f"Algoritmo desconocido: {algorithm!r}")
    return ALGORITMOS[algorithm]
