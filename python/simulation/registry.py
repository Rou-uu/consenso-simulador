"""Registro de algoritmos de consenso.

Modulo hoja: no importa nada del paquete, para que ``algorithms`` pueda
registrarse sin crear un ciclo con ``engine`` ni ``sweep``.

Un algoritmo se describe con un ``AlgorithmSpec`` que reune sus tres piezas:

``scalar``
    Una ronda nodo por nodo, para el editor.
``batch``
    La version vectorizada con NumPy, para el sweep. Es opcional: un algoritmo
    sin ella simplemente no aparece en el barrido.
``updated``
    Predicado que decide si una arista entregada cuenta como actualizacion del
    valor del target. Es contrato de visualizacion, no de matematica.

Ademas declara ``label`` y ``params``, que la interfaz usa para armar sola el
selector y los campos de parametros.

Las vistas ``KEYS``, ``SCALAR``, ``BATCH`` y ``UPDATED`` son *vivas*: reflejan
lo que haya en el registro en el momento de consultarlas, no una copia tomada
al importar. Eso permite que ``engine`` y ``sweep`` se importen antes que
``algorithms`` sin congelar un registro vacio.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Callable, Iterator


@dataclass(frozen=True)
class ParamSpec:
    """Un parametro propio del algoritmo, tal como lo pedira la interfaz."""

    name: str
    label: str
    default: Any
    step: str = "any"


@dataclass(frozen=True)
class AlgorithmSpec:
    """Descripcion completa de un algoritmo de consenso."""

    key: str
    label: str
    scalar: Callable[..., Any]
    updated: Callable[..., Any]
    batch: Callable[..., Any] | None = None
    params: tuple[ParamSpec, ...] = ()

    def defaults(self) -> dict[str, Any]:
        """Valores por omision de sus parametros, listos para usar como ``params``."""

        return {param.name: param.default for param in self.params}


REGISTRY: dict[str, AlgorithmSpec] = {}

#: Clave reservada para el algoritmo que escribe quien usa la aplicacion. Es
#: fija y unica a proposito: con una sola ranura no hay colisiones que resolver
#: ni forma de que un algoritmo pegado tape a `fv` o a `amp`. La instala
#: `simulation.usuario`; `register` no la trata distinto.
CLAVE_USUARIO = "user_alg"


def register(spec: AlgorithmSpec) -> AlgorithmSpec:
    """Agrega un algoritmo al registro y lo devuelve.

    Rechaza claves repetidas: un algoritmo que pisara a otro en silencio seria
    imposible de diagnosticar desde la interfaz.
    """

    if spec.key in REGISTRY:
        raise ValueError(f"Algoritmo ya registrado: {spec.key!r}")
    REGISTRY[spec.key] = spec
    return spec


def get(key: str) -> AlgorithmSpec:
    """Devuelve el spec de ``key`` o eleva ``ValueError`` con el mensaje habitual."""

    try:
        return REGISTRY[key]
    except KeyError as exc:
        raise ValueError(f"Algoritmo desconocido: {key!r}") from exc


def batch_of(key: str) -> Callable[..., Any]:
    """Devuelve la funcion batched de ``key``, o explica por que no la hay.

    Distingue dos situaciones que antes se confundian en un solo mensaje:
    que el algoritmo no exista, y que exista pero no tenga version
    vectorizada. La segunda es normal —``batch`` es opcional— y el usuario
    necesita saber que su algoritmo si esta registrado, solo que el barrido
    no puede usarlo.
    """

    spec = REGISTRY.get(key)
    if spec is None:
        raise ValueError(f"Algoritmo desconocido: {key!r}")
    if spec.batch is None:
        raise ValueError(
            f"El algoritmo {key!r} ({spec.label}) no tiene version vectorizada, "
            "asi que no puede usarse en el barrido. Funciona en el editor."
        )
    return spec.batch


def specs() -> tuple[AlgorithmSpec, ...]:
    """Todos los algoritmos registrados, en orden de registro."""

    return tuple(REGISTRY.values())


class _AttributeView(Mapping):
    """Vista viva ``key -> spec.<atributo>`` que omite los que no lo definen."""

    def __init__(self, attribute: str) -> None:
        self._attribute = attribute

    def __getitem__(self, key: str) -> Any:
        spec = REGISTRY.get(key)
        value = None if spec is None else getattr(spec, self._attribute)
        if value is None:
            raise KeyError(key)
        return value

    def __iter__(self) -> Iterator[str]:
        return (
            key
            for key, spec in REGISTRY.items()
            if getattr(spec, self._attribute) is not None
        )

    def __len__(self) -> int:
        return sum(
            1 for spec in REGISTRY.values() if getattr(spec, self._attribute) is not None
        )

    def __repr__(self) -> str:
        return f"<vista {self._attribute}: {', '.join(self) or 'vacia'}>"


#: Claves registradas. Vista viva del dict, valida para ``x in ALGORITHMS``.
KEYS = REGISTRY.keys()
SCALAR = _AttributeView("scalar")
BATCH = _AttributeView("batch")
UPDATED = _AttributeView("updated")


__all__ = [
    "BATCH",
    "CLAVE_USUARIO",
    "KEYS",
    "REGISTRY",
    "SCALAR",
    "UPDATED",
    "AlgorithmSpec",
    "ParamSpec",
    "batch_of",
    "get",
    "register",
    "specs",
]
