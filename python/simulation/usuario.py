"""El algoritmo que escribe quien usa la aplicación (no existe en api.py).

El fuente pegado define un diccionario ``ALGORITMO`` con las mismas claves que
una entrada de ``algoritmos.ALGORITMOS``::

    def mi_regla(matrix, values, messages, params):
        ...
        return [(valor_nuevo, None), ...]

    ALGORITMO = {"nombre": "Mi algoritmo", "ronda": mi_regla}

Se instala siempre bajo la clave fija ``user_alg``: no puede tapar a ``fv`` ni a
``amp``. Corre solo dentro del Worker; en el hilo principal nunca se ejecuta.
"""

import numpy as np

from .algoritmos import ALGORITMOS

CLAVE = "user_alg"


def instalar(fuente: str) -> dict:
    """Compila ``fuente`` y deja su ``ALGORITMO`` en ``ALGORITMOS["user_alg"]``.

    Eleva ``SyntaxError`` si no compila, la excepción que sea si revienta al
    ejecutarse, y ``ValueError`` si no define un ``ALGORITMO`` usable.
    """
    # `<algoritmo>` aparece en el traceback: así el error apunta a la línea pegada.
    codigo = compile(fuente, "<algoritmo>", "exec")
    # Se vacía ANTES de ejecutar: si el fuente falla, no queda corriendo el anterior.
    ALGORITMOS.pop(CLAVE, None)
    espacio = {"np": np, "numpy": np, "__name__": "algoritmo_propio"}
    exec(codigo, espacio)  # noqa: S102 - ejecutar código pegado es el objetivo

    algoritmo = espacio.get("ALGORITMO")
    if not isinstance(algoritmo, dict) or "ronda" not in algoritmo:
        raise ValueError(
            "El código debe terminar definiendo el algoritmo, por ejemplo:\n"
            '    ALGORITMO = {"nombre": "Mi algoritmo", "ronda": mi_regla}'
        )
    if algoritmo.get("parametros"):
        # La interfaz arma sus campos en el hilo principal, que no ve este código:
        # el algoritmo correría con valores que nadie eligió.
        raise ValueError(
            "Los parametros todavia no se pueden editar desde la interfaz. "
            "Pon la constante directo en tu codigo:\n"
            "    PESO = 1.0"
        )
    ALGORITMOS[CLAVE] = {"nombre": "Algoritmo propio", **algoritmo}
    return ALGORITMOS[CLAVE]
