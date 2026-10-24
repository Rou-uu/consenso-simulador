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
}


def buscar(algorithm: str) -> dict:
    """La entrada de ``algorithm``, o un error que lo dice."""
    # Cambio respecto a api.py: ahí un nombre desconocido caía en silencio en FV.
    if algorithm not in ALGORITMOS:
        raise ValueError(f"Algoritmo desconocido: {algorithm!r}")
    return ALGORITMOS[algorithm]
