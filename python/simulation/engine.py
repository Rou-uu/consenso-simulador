"""Motor puro de propagacion para la aplicacion client-side.

Este modulo porta la semantica de ``back/api.py`` sin depender de FastAPI ni
del DOM. Las funciones de bajo nivel trabajan con arreglos NumPy; las APIs
``run_round`` y ``simulate_graph`` convierten todos sus resultados a ``dict``,
``list``, ``bool``, ``int`` y ``float`` nativos para que Pyodide pueda
transferirlos al navegador sin filtrar escalares NumPy.

Convenciones importantes
------------------------
* ``matrix[source, target]`` contiene la probabilidad/peso de una arista.
* ``messages[target, source]`` indica que el target recibio el mensaje. La
  transposicion es deliberada y coincide con el backend original.
* En grafos no dirigidos se reutiliza la misma muestra aleatoria para ambas
  direcciones de una arista. Por ello el disparo es simetrico.
* Pesos repetidos entre el mismo par de nodos se sobrescriben en el orden del
  JSON, igual que al asignar sobre una matriz en el backend.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping, Sequence

import numpy as np

from . import registry
from .algorithms import amp, amp_updated, fv, fv_updated  # noqa: F401 (re-export)
from .rng import native_scalar as _native_scalar
from .rng import random_array as _random_array
from .rng import random_index as _random_index  # noqa: F401 (compatibilidad)


#: Claves registradas. Vista viva: un algoritmo nuevo aparece aqui solo.
ALGORITHMS = registry.KEYS

#: Predicado de propagacion por algoritmo. Vista viva sobre el registro.
PROPAGATION_PREDICATES = registry.UPDATED


def get_adj(data: Mapping[str, Any]) -> np.ndarray:
    """Construye la matriz de adyacencia del JSON del editor.

    El orden de filas y columnas es exactamente el orden de ``data["nodes"]``.
    Los identificadores pueden ser strings o numeros, pero deben ser unicos y
    toda arista debe referirse a un nodo existente. Un grafo no dirigido copia
    cada peso en ambas direcciones. Los pesos se convierten a ``float``; pesos
    ausentes, ids desconocidos o estructuras incompletas elevan la excepcion
    natural de Python, como en el backend de referencia.

    Un grafo sin nodos produce una matriz ``(0, 0)``. Los self-loops se
    conservan y, en un grafo no dirigido, simplemente vuelven a escribir la
    misma celda.
    """

    ids = [node["id"] for node in data["nodes"]]
    indices = {node_id: index for index, node_id in enumerate(ids)}
    directed = data["directed"]

    matrix = np.zeros((len(ids), len(ids)))
    for edge in data["edges"]:
        source = indices[edge["source"]]
        target = indices[edge["target"]]
        weight = float(edge["weight"])
        matrix[source, target] = weight
        if not directed:
            matrix[target, source] = weight

    return matrix


def get_values(data: Mapping[str, Any]) -> np.ndarray:
    """Devuelve los valores de nodo en el mismo orden usado por ``get_adj``.

    No fuerza un dtype para mantener el comportamiento original de
    ``np.array``. Las APIs de simulacion terminan convirtiendo los valores
    adoptados a ``float``, tal como hacia ``update_graph_data`` en FastAPI.
    """

    return np.array([node["value"] for node in data["nodes"]])


def who_gets_message(
    matrix: np.ndarray,
    directed: bool,
    *,
    rng: Any | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """Sortea las aristas que transmiten durante una ronda.

    Devuelve ``(messages, probabilities)``. ``messages`` ya esta transpuesta:
    ``messages[target, source] == 1`` significa que ``source`` transmitio a
    ``target``. ``probabilities`` permanece en orientacion
    ``[source, target]``, exactamente como en el backend.

    La mascara ``ceil(matrix)`` se conserva literalmente. Para los pesos
    normales en ``[0, 1]`` equivale a muestrear U(0, 1) solo donde existe una
    arista; pesos cero no disparan y peso uno dispara con probabilidad uno. El
    comportamiento de pesos fuera de ese intervalo tambien queda delegado a
    esa expresion original, en vez de recortarlos silenciosamente.

    ``rng`` es opcional y se ofrece solo para ejecuciones reproducibles. Si se
    omite, se usa ``np.random`` y por tanto ``np.random.seed`` sigue funcionando.
    """

    matrix = np.asarray(matrix)
    adjacency_mask = np.ceil(matrix)
    probabilities = _random_array(rng, matrix.shape) * adjacency_mask

    if not directed:
        upper = np.triu(probabilities)
        probabilities = upper + upper.T

    messages = (matrix > probabilities).astype(int).T
    return messages, probabilities


def propagation_from_round(
    node_ids: Sequence[Any],
    old_values: np.ndarray,
    messages: np.ndarray,
    updates: Sequence[tuple[Any, int | None]],
    algorithm: str,
    params: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Describe cada mensaje enviado con el contrato observable del backend.

    Se emite un registro por arista que disparo, en orden target-major y luego
    source-major. Que una arista entregada cuente ademas como *actualizacion*
    depende del algoritmo, y esa regla vive en ``PROPAGATION_PREDICATES``: este
    bucle solo recorre las aristas y arma el registro final.

    Los ids se conservan y los numeros/banderas se convierten a tipos Python.
    El resultado no contiene matrices ni escalares NumPy.
    """

    if algorithm not in ALGORITHMS:
        raise ValueError(f"Algoritmo desconocido: {algorithm!r}")

    old_values = np.asarray(old_values)
    messages = np.asarray(messages)
    predicate = PROPAGATION_PREDICATES[algorithm]
    propagation: list[dict[str, Any]] = []

    for target_index in range(len(messages)):
        for source_index in range(len(messages[target_index])):
            if messages[target_index, source_index] != 1:
                continue

            updated_value = predicate(
                messages, updates, old_values, target_index, source_index, params
            )

            propagation.append(
                {
                    "source": _native_scalar(node_ids[source_index]),
                    "target": _native_scalar(node_ids[target_index]),
                    "value_sent": float(old_values[source_index]),
                    "updated_value": bool(updated_value),
                }
            )

    return propagation


def discrepancy(values: Sequence[Any] | np.ndarray) -> float:
    """Calcula ``max(values) - min(values)`` como ``float`` nativo.

    La discrepancia no esta definida para una coleccion vacia y en ese caso se
    eleva ``ValueError``. La UI solo habilita simulaciones con nodos.
    """

    numeric_values = np.asarray(values, dtype=float)
    if len(numeric_values) == 0:
        raise ValueError("No se puede calcular discrepancia sin nodos")
    return float(numeric_values.max() - numeric_values.min())


def run_round(
    matrix: np.ndarray | Mapping[str, Any],
    values: np.ndarray | None = None,
    directed: bool | None = None,
    algorithm: str = "fv",
    params: Mapping[str, Any] | None = None,
    *,
    node_ids: Sequence[Any] | None = None,
    rng: Any | None = None,
) -> dict[str, Any]:
    """Ejecuta una ronda y devuelve un resultado transferible al navegador.

    ``matrix`` puede ser la matriz numerica o el grafo JSON completo. En el
    segundo caso ``values``, ``directed`` y ``node_ids`` se derivan del grafo,
    lo que permite al controlador llamar simplemente
    ``run_round(graph, algorithm="fv")``. Con una matriz, ``values`` y
    ``directed`` son obligatorios.

    ``params`` es el diccionario de parametros propios del algoritmo (por
    ejemplo ``{"agreed": 0.5}`` para AMP). Cada algoritmo lee de ahi lo que
    necesita e ignora el resto, de modo que agregar un algoritmo con parametros
    nuevos no cambia ninguna firma de esta cadena.

    El resultado contiene:

    ``values``
        Valores finales como ``float`` nativos.
    ``selectedSources``
        Indice del emisor elegido por target en FV o ``None``.
    ``messages``
        Matriz entera ``[target][source]``.
    ``probabilities``
        Muestras uniformes/multiplicadas en orientacion ``[source][target]``.
    ``valuesBefore``, ``discrepancy`` y ``algorithm``
        Campos usados directamente por la UI y por ``run_simulation``.
    ``propagation``
        Registros con ids. Si ``node_ids`` se omite se usan indices ``0..n-1``.

    Los grafos vacios son validos aqui y producen colecciones vacias, igual que
    el endpoint individual original; su ``discrepancy`` es ``None`` porque no
    existe un rango numerico y ``None`` mantiene serializacion JSON estricta.
    Un nombre de algoritmo invalido eleva ``ValueError`` antes de consumir
    aleatoriedad.
    """

    if algorithm not in ALGORITHMS:
        raise ValueError(f"Algoritmo desconocido: {algorithm!r}")

    if isinstance(matrix, Mapping):
        graph = matrix
        if values is not None or directed is not None:
            raise ValueError(
                "values y directed se omiten cuando run_round recibe un grafo"
            )
        matrix = get_adj(graph)
        values = get_values(graph)
        directed = bool(graph["directed"])
        if node_ids is None:
            node_ids = [node["id"] for node in graph["nodes"]]
    elif values is None or directed is None:
        raise ValueError(
            "values y directed son obligatorios cuando run_round recibe una matriz"
        )

    matrix = np.asarray(matrix)
    values = np.asarray(values)
    if matrix.shape != (len(values), len(values)):
        raise ValueError(
            "La matriz debe ser cuadrada y coincidir con la cantidad de valores"
        )

    ids = list(range(len(values))) if node_ids is None else list(node_ids)
    if len(ids) != len(values):
        raise ValueError("node_ids debe tener la misma longitud que values")

    messages, probabilities = who_gets_message(matrix, directed, rng=rng)
    # El despacho no conoce ningun algoritmo por nombre: lo pide al registro y
    # lo llama con la firma uniforme.
    algorithm_fn = registry.get(algorithm).scalar
    updates = algorithm_fn(matrix, values, messages, params, rng=rng)

    return {
        "values": [float(value) for value, _ in updates],
        "valuesBefore": [float(value) for value in values],
        "selectedSources": [
            None if source is None else int(source) for _, source in updates
        ],
        "messages": messages.astype(int).tolist(),
        "probabilities": probabilities.astype(float).tolist(),
        "propagation": propagation_from_round(
            ids, values, messages, updates, algorithm, params
        ),
        "discrepancy": (
            discrepancy([value for value, _ in updates]) if updates else None
        ),
        "algorithm": algorithm,
    }


def update_graph_data(
    data: Mapping[str, Any],
    new_values: Sequence[Any],
) -> dict[str, Any]:
    """Copia el grafo y reemplaza sus valores sin mutar el argumento.

    ``new_values`` acepta tanto la lista plana que entrega ``run_round`` como
    las tuplas ``(value, selected_source)`` de ``fv``/``amp``. Un valor NaN no
    sobrescribe el nodo, reproduciendo la salvaguarda del backend original.
    La cantidad de valores debe cubrir todos los nodos; de lo contrario se
    propaga ``IndexError``.
    """

    updated_data = deepcopy(dict(data))

    for index, node in enumerate(updated_data["nodes"]):
        entry = new_values[index]
        if isinstance(entry, (tuple, list)):
            value = entry[0]
        else:
            value = entry

        try:
            is_nan = bool(np.isnan(value))
        except TypeError:
            is_nan = False
        if not is_nan:
            node["value"] = float(value)

    return updated_data


def simulate_graph(
    data: Mapping[str, Any],
    algorithm: str = "fv",
    params: Mapping[str, Any] | None = None,
    *,
    rng: Any | None = None,
) -> dict[str, Any]:
    """Ejecuta la simulacion individual con el contrato de ``POST /simulate``.

    La operacion siempre representa una sola ronda. Devuelve un grafo nuevo,
    no muta ``data`` y conserva exactamente los campos observables del backend:
    ``success``, ``graph``, ``propagation``, ``algorithm`` y ``message``.
    Excepciones de validacion se dejan subir para que la capa de interfaz pueda
    mostrarlas; no se introduce una dependencia HTTP.
    """

    if algorithm not in ALGORITHMS:
        raise ValueError(f"Algoritmo desconocido: {algorithm!r}")

    matrix = get_adj(data)
    values = get_values(data)
    node_ids = [node["id"] for node in data["nodes"]]
    result = run_round(
        matrix,
        values,
        bool(data["directed"]),
        algorithm,
        params,
        node_ids=node_ids,
        rng=rng,
    )
    updated_graph = update_graph_data(data, result["values"])

    return {
        "success": True,
        "graph": updated_graph,
        "propagation": result["propagation"],
        "algorithm": algorithm,
        "message": "Simulación completada exitosamente",
    }


def run_simulation(
    data: Mapping[str, Any],
    algorithm: str = "fv",
    params: Mapping[str, Any] | None = None,
    *,
    rounds: int = 100,
    rng: Any | None = None,
) -> dict[str, Any]:
    """Ejecuta varias rondas y conserva el historial visible de la UI.

    Esta funcion complementa ``simulate_graph`` (una ronda con el contrato del
    endpoint). El resultado sigue el contrato del motor client-side original:
    ``{"history": [...], "algorithm": ...}``. La entrada inicial ocupa
    ``roundNum=0`` y cada ronda agrega valores previos, propagacion y
    discrepancia. La matriz/topologia permanece fija; solo evolucionan los
    valores de nodo.

    ``rounds`` acepta cero para obtener exclusivamente el estado inicial. Un
    grafo vacio se rechaza porque su discrepancia no es finita ni serializable.
    """

    if algorithm not in ALGORITHMS:
        raise ValueError(f"Algoritmo desconocido: {algorithm!r}")
    if isinstance(rounds, bool) or not isinstance(rounds, (int, np.integer)):
        raise ValueError("rounds debe ser entero")
    rounds = int(rounds)
    if rounds < 0:
        raise ValueError("rounds no puede ser negativo")

    matrix = get_adj(data)
    current_values = get_values(data).astype(float)
    if len(current_values) == 0:
        raise ValueError("El grafo no tiene nodos")
    node_ids = [node["id"] for node in data["nodes"]]
    directed = bool(data["directed"])

    history: list[dict[str, Any]] = [
        {
            "values": current_values.tolist(),
            "valuesBefore": None,
            "propagation": [],
            "discrepancy": discrepancy(current_values),
            "roundNum": 0,
        }
    ]

    for round_index in range(rounds):
        result = run_round(
            matrix,
            current_values,
            directed,
            algorithm,
            params,
            node_ids=node_ids,
            rng=rng,
        )
        current_values = np.asarray(result["values"], dtype=float)
        history.append(
            {
                "values": list(result["values"]),
                "valuesBefore": list(result["valuesBefore"]),
                "propagation": list(result["propagation"]),
                "discrepancy": float(result["discrepancy"]),
                "roundNum": round_index + 1,
            }
        )

    return {"history": history, "algorithm": algorithm}


# Nombre corto para controladores que reflejan el endpoint original.
simulate = simulate_graph


__all__ = [
    "ALGORITHMS",
    "PROPAGATION_PREDICATES",
    "amp",
    "amp_updated",
    "discrepancy",
    "fv",
    "fv_updated",
    "get_adj",
    "get_values",
    "propagation_from_round",
    "run_round",
    "run_simulation",
    "simulate",
    "simulate_graph",
    "update_graph_data",
    "who_gets_message",
]
