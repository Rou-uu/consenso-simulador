"""Instala un algoritmo escrito por quien usa la aplicación.

Este módulo es la **ranura de código propio**: compila un fuente pegado y deja
su algoritmo en el registro bajo una clave fija, ``user_alg``. Nunca crea claves
nuevas, así que un algoritmo pegado no puede tapar a ``fv`` ni a ``amp`` por
mucho que su código diga otra cosa.

Corre **dentro del Worker**, nunca en el hilo principal (ver `docs/09` §A.12).
Un Worker no tiene ``document``, así que el aislamiento lo da el entorno y no una
lista de permisos que haya que mantener.

El fuente se compila con el nombre de archivo ``<algoritmo>`` para que el
traceback apunte a las líneas de quien lo escribió y no a un ``<string>``
anónimo. Sin eso, depurar código propio es adivinar.

Lo que se ejecuta es exactamente la misma forma que usa ``algorithms.py``:

    register(AlgorithmSpec(
        key="lo que sea",          # se ignora: la ranura siempre es user_alg
        label="Mi algoritmo",
        scalar=mi_funcion,
        batch=mi_funcion_vectorizada,   # opcional; sin ella no hay barrido
    ))

``updated`` también es opcional aquí, a diferencia de ``AlgorithmSpec``: si no se
declara se usa «el valor del target cambió», que es lo que quiere casi todo el
mundo. Es contrato de visualización, no de matemática.

``params``, en cambio, se **rechaza**: la interfaz arma sus campos desde el
registro del hilo principal, que no ve lo que se compila aquí. Aceptarlos haría
correr el algoritmo con los valores por omisión sin que nadie entendiera por qué.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

import numpy as np

from . import registry
from .registry import AlgorithmSpec, ParamSpec
from .rng import native_scalar, random_array, random_index


#: La ranura, definida en el registro para que la interfaz la conozca sin
#: importar este módulo.
CLAVE = registry.CLAVE_USUARIO

ETIQUETA_POR_OMISION = "Algoritmo propio"


def actualizado_por_defecto(
    messages: np.ndarray,
    updates: Sequence[tuple[Any, int | None]],
    old_values: np.ndarray,
    target_index: int,
    source_index: int,
    params: Mapping[str, Any] | None = None,
) -> bool:
    """Predicado de visualización cuando el algoritmo no declara el suyo.

    Cuenta como actualización si el valor del target cambió. Es el criterio de
    AMP, y el razonable por omisión: una arista que entregó un mensaje que no
    movió nada se dibuja como entregada, no como actualización.
    """

    del messages, source_index, params
    nuevo, _ = updates[target_index]
    previo = old_values[target_index]
    try:
        return not bool(np.isclose(nuevo, previo))
    except (TypeError, ValueError):
        # Valores no numéricos (un JSON ajeno puede traer strings): comparar
        # directo es lo único que queda, y es mejor que reventar el render.
        return nuevo != previo


def desinstalar() -> None:
    """Vacía la ranura. Es idempotente: sirve tanto si había algo como si no."""

    registry.REGISTRY.pop(CLAVE, None)


def instalar(fuente: str) -> AlgorithmSpec:
    """Compila ``fuente`` y deja su algoritmo en la ranura ``user_alg``.

    Reemplaza lo que hubiera: el ciclo natural es pegar, ver, corregir y volver
    a pegar, y ``registry.register`` rechaza claves repetidas a propósito. Esa
    severidad se conserva para los algoritmos de casa; aquí el reemplazo es
    explícito.

    Eleva ``SyntaxError`` si el fuente no compila, la excepción que sea si
    revienta al ejecutarse, y ``ValueError`` si no registró ningún algoritmo.
    """

    capturados: list[AlgorithmSpec] = []

    def spec_permisivo(
        key: str = CLAVE,
        label: str = ETIQUETA_POR_OMISION,
        scalar: Any = None,
        updated: Any = None,
        batch: Any = None,
        params: tuple[ParamSpec, ...] = (),
    ) -> AlgorithmSpec:
        """``AlgorithmSpec`` con ``updated`` opcional, para código pegado."""

        if scalar is None:
            raise ValueError(
                "El algoritmo necesita al menos `scalar`: la regla que aplica "
                "un nodo cuando recibe mensajes."
            )
        if params:
            # Declararlos y que no aparezca ningun campo seria peor que negarse:
            # el algoritmo correria con los valores por omision y nadie sabria
            # por que. La interfaz arma sus campos desde el registro del hilo
            # principal, que no ve lo que se compila aqui.
            raise ValueError(
                "Los parametros declarados todavia no se pueden editar desde la "
                "interfaz. Pon la constante directo en tu codigo:\n"
                "    peso = 1.0    # en vez de ParamSpec(name=\"peso\", ...)"
            )
        return AlgorithmSpec(
            key=key,
            label=label,
            scalar=scalar,
            updated=updated or actualizado_por_defecto,
            batch=batch,
            params=tuple(params),
        )

    espacio: dict[str, Any] = {
        "AlgorithmSpec": spec_permisivo,
        "ParamSpec": ParamSpec,
        "register": capturados.append,
        "np": np,
        "numpy": np,
        "random_array": random_array,
        "random_index": random_index,
        "native_scalar": native_scalar,
        "__name__": "algoritmo_propio",
    }

    # El nombre de archivo viaja en el traceback: sin él, un error en el código
    # pegado aparece como `<string>` y sin contexto.
    codigo = compile(fuente, "<algoritmo>", "exec")
    # Vaciar antes de ejecutar, no después: si el fuente falla, es mejor que la
    # siguiente ronda diga «algoritmo desconocido» a que corra en silencio la
    # versión anterior haciéndose pasar por la recién pegada.
    desinstalar()
    exec(codigo, espacio)  # noqa: S102 - ejecutar código pegado es el objetivo

    if not capturados:
        raise ValueError(
            "El código no registró ningún algoritmo. Falta la llamada final:\n"
            "    register(AlgorithmSpec(label=\"...\", scalar=mi_funcion))"
        )

    # Se queda el último: si alguien registró varios, el de abajo es el que
    # estaba escribiendo. La clave se fuerza aquí y en ningún otro lado.
    spec = capturados[-1]
    instalado = AlgorithmSpec(
        key=CLAVE,
        label=spec.label,
        scalar=spec.scalar,
        updated=spec.updated,
        batch=spec.batch,
        params=spec.params,
    )
    registry.REGISTRY[CLAVE] = instalado
    return instalado


__all__ = [
    "CLAVE",
    "ETIQUETA_POR_OMISION",
    "actualizado_por_defecto",
    "desinstalar",
    "instalar",
]
