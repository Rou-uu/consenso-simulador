"""Algoritmos de consenso.

**Este es el archivo que se edita para agregar un algoritmo.** No hay que tocar
el motor, el sweep, la interfaz ni el JavaScript: basta escribir aqui las piezas
y llamar a ``register``.

Un algoritmo se compone de hasta tres funciones, todas con firma uniforme:

``scalar(matrix, values, messages, params, *, rng)``
    Una ronda nodo por nodo. ``messages[target, source] == 1`` significa que
    ``source`` transmitio a ``target`` (la matriz llega ya transpuesta).
    Devuelve una lista de ``(valor_nuevo, indice_del_emisor_elegido | None)``.

``batch(values, fired, params, *, rng)``
    La misma regla, vectorizada sobre ``S`` replicas. Ojo con la orientacion:
    aqui ``fired[s, source, target]`` **no** esta transpuesta. Devuelve un
    arreglo ``(S, n)``. Es opcional; sin ella el algoritmo no aparece en el
    sweep.

``updated(messages, updates, old_values, target, source, params)``
    Predicado de visualizacion: ¿esta arista entregada cuenta como
    actualizacion del valor del target? Decide como se colorea la arista, no la
    matematica.

Cada funcion recibe el diccionario ``params`` completo y toma de ahi lo que
necesita, asi que un algoritmo con parametros nuevos no cambia ninguna firma de
la cadena. Los parametros se declaran en el ``AlgorithmSpec`` y la interfaz arma
sus campos sola.

La matematica de FV y AMP es un port literal de ``pruebas.ipynb``; se conserva
tal cual aunque hubiera formas mas limpias de escribirla.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

import numpy as np

from .registry import AlgorithmSpec, ParamSpec, register
from .rng import native_scalar, random_array, random_index


AGREED_DEFAULT = 0.5


# ==========================================================================
# Flip Value (FV)
# ==========================================================================

def fv(
    matrix: np.ndarray,
    values: np.ndarray,
    messages: np.ndarray,
    params: Mapping[str, Any] | None = None,
    *,
    rng: Any | None = None,
) -> list[tuple[Any, int | None]]:
    """Cada target que recibio mensajes elige un emisor al azar y copia su valor.

    Un target sin mensajes conserva su propio valor. El segundo elemento de cada
    tupla es el indice del emisor elegido, o ``None`` cuando no hubo eleccion:
    el backend usaba ``NaN`` como centinela y ``None`` preserva la decision
    siendo ademas JSON-serializable.

    ``matrix`` y ``params`` se aceptan por la firma uniforme; FV no los usa.
    """

    del matrix, params
    values = np.asarray(values)
    messages = np.asarray(messages)
    new_values: list[tuple[Any, int | None]] = []

    for target_index, received in enumerate(messages):
        valid_sources = np.where(received != 0)[0]
        if len(valid_sources) == 0:
            new_values.append((native_scalar(values[target_index]), None))
            continue

        source_index = int(valid_sources[random_index(rng, len(valid_sources))])
        new_values.append((native_scalar(values[source_index]), source_index))

    return new_values


def fv_batch(
    values: np.ndarray,
    fired: np.ndarray,
    params: Mapping[str, Any] | None = None,
    *,
    rng: Any | None = None,
) -> np.ndarray:
    """FV vectorizado con seleccion uniforme entre emisores recibidos.

    El maximo de llaves uniformes independientes selecciona cada emisor con la
    misma probabilidad, reproduciendo el backend. Si no hubo mensaje,
    ``received`` conserva el valor previo aunque ``argmax`` haya devuelto cero.
    """

    del params
    fired = np.asarray(fired)
    received = fired.any(axis=1)
    keys = fired * random_array(rng, fired.shape)
    chosen = keys.argmax(axis=1)
    adopted = np.take_along_axis(values, chosen, axis=1)
    return np.where(received, adopted, values)


def fv_updated(
    messages: np.ndarray,
    updates: Sequence[tuple[Any, int | None]],
    old_values: np.ndarray,
    target_index: int,
    source_index: int,
    params: Mapping[str, Any] | None = None,
) -> bool:
    """Solo el emisor sorteado cuenta como actualizacion.

    ``selected_source`` es ``None`` cuando el target no eligio a nadie, y
    ``None == int`` es ``False``, asi que ese caso se resuelve solo.
    """

    del messages, old_values, params
    _, selected_source = updates[target_index]
    return selected_source == source_index


register(
    AlgorithmSpec(
        key="fv",
        label="Flip Value (FV)",
        scalar=fv,
        batch=fv_batch,
        updated=fv_updated,
        params=(),
    )
)


# ==========================================================================
# Agreement (AMP)
# ==========================================================================

def amp(
    matrix: np.ndarray,
    values: np.ndarray,
    messages: np.ndarray,
    params: Mapping[str, Any] | None = None,
    *,
    rng: Any | None = None,
) -> list[tuple[Any, None]]:
    """Todo target que recibio al menos un mensaje adopta ``agreed``.

    AMP no selecciona emisor, por lo que el indice es siempre ``None``.
    ``matrix`` y ``rng`` se conservan por la firma uniforme.
    """

    del matrix, rng
    values = np.asarray(values)
    messages = np.asarray(messages)
    agreed = native_scalar((params or {}).get("agreed", AGREED_DEFAULT))
    new_values: list[tuple[Any, None]] = []

    for target_index, received in enumerate(messages):
        if np.sum(received) != 0:
            new_values.append((agreed, None))
        else:
            new_values.append((native_scalar(values[target_index]), None))

    return new_values


def amp_batch(
    values: np.ndarray,
    fired: np.ndarray,
    params: Mapping[str, Any] | None = None,
    *,
    rng: Any | None = None,
) -> np.ndarray:
    """AMP vectorizado: todo target que recibio adopta ``agreed``."""

    del rng
    agreed = (params or {}).get("agreed", AGREED_DEFAULT)
    received = np.asarray(fired).any(axis=1)
    return np.where(received, agreed, values)


def amp_updated(
    messages: np.ndarray,
    updates: Sequence[tuple[Any, int | None]],
    old_values: np.ndarray,
    target_index: int,
    source_index: int,
    params: Mapping[str, Any] | None = None,
) -> bool:
    """Todos los mensajes que llegaron al target comparten el mismo flag.

    Es ``True`` solo si el valor visible del target cambio al consenso, de modo
    que un mensaje recibido por un nodo que ya estaba en ``agreed`` se marca
    como entregado pero no como actualizacion.
    """

    del messages, source_index, params
    new_value, _ = updates[target_index]
    return not bool(np.isclose(new_value, old_values[target_index]))


register(
    AlgorithmSpec(
        key="amp",
        label="Agreement (AMP)",
        scalar=amp,
        batch=amp_batch,
        updated=amp_updated,
        params=(
            ParamSpec(
                name="agreed",
                label="Valor de consenso (<code>agreed</code>)",
                default=AGREED_DEFAULT,
            ),
        ),
    )
)


__all__ = [
    "AGREED_DEFAULT",
    "amp",
    "amp_batch",
    "amp_updated",
    "fv",
    "fv_batch",
    "fv_updated",
]
