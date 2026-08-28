"""Muestreo aleatorio compartido por el motor escalar y el batched.

Modulo hoja: no importa nada del paquete. Existe para que ``algorithms`` pueda
usar estos helpers sin depender de ``engine`` ni de ``sweep``, y de paso elimina
la copia identica de ``random_array`` que vivia en ambos.

Todas las funciones aceptan ``rng=None``, en cuyo caso usan ``np.random``. Esa
rama es la que preserva ``np.random.seed()`` y, con ella, la reproducibilidad
del backend original.
"""

from __future__ import annotations

from typing import Any

import numpy as np


def random_array(rng: Any | None, shape: tuple[int, ...]) -> np.ndarray:
    """Muestra uniformes con el RNG global o con un Generator compatible."""

    if rng is None:
        # rand(*shape), en vez de default_rng(), preserva np.random.seed().
        return np.random.rand(*shape)
    return np.asarray(rng.random(shape))


def random_index(rng: Any | None, high: int) -> int:
    """Selecciona un indice preservando RandomState y Generator."""

    if rng is None:
        return int(np.random.randint(0, high))
    integers = getattr(rng, "integers", None)
    if integers is not None:
        return int(integers(0, high))
    return int(rng.randint(0, high))


def native_scalar(value: Any) -> Any:
    """Extrae el escalar Python de un escalar NumPy, si corresponde."""

    if isinstance(value, np.generic):
        return value.item()
    return value


__all__ = ["native_scalar", "random_array", "random_index"]
