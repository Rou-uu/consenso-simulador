"""Una ronda de simulación: el endpoint ``POST /simulate`` de ``back/api.py``.

Lo que hace una ronda, en tres pasos:

1. ``who_gets_message``: se sortea qué aristas transmiten.
2. ``ronda`` del algoritmo: cada nodo decide su valor nuevo con lo que recibió.
3. ``propagacion``: se describe cada mensaje enviado, para colorear las aristas.

``run_round`` es lo que llama la interfaz (desde el Worker). ``simulate_graph``
conserva la respuesta exacta del endpoint original. Cada diferencia con el
backend está marcada con ``# Cambio respecto a api.py``.

Convención de la matriz: ``matrix[source, target]`` es el peso de la arista, y
``messages[target, source] == 1`` significa que ``target`` recibió de ``source``
(transpuesta, igual que en el backend).
"""

from copy import deepcopy

import numpy as np

from .algoritmos import buscar, cambio_el_valor


def get_adj(data: dict) -> np.ndarray:
    """
    Convierte el grafo JSON a matriz de adyacencia.
    Lógica exacta de pruebas.ipynb
    """
    ids = [node['id'] for node in data['nodes']]
    indices = {v: i for i, v in enumerate(ids)}
    directed = data['directed']

    n = len(ids)
    adj = np.zeros((n, n))

    for edge in data['edges']:
        s = indices[edge['source']]
        t = indices[edge['target']]
        adj[s, t] = float(edge['weight'])
        if not directed:
            adj[t, s] = float(edge['weight'])

    return adj


def get_values(data: dict) -> np.ndarray:
    """
    Extrae los valores de los nodos.
    Lógica exacta de pruebas.ipynb
    """
    return np.array([node['value'] for node in data['nodes']])


def who_gets_message(matrix: np.ndarray, directed: bool) -> tuple:
    """
    Determina probabilísticamente qué mensajes se envían.
    Devuelve la matriz de mensajes ya TRANSPUESTA: messages[i] = lo que recibe el nodo i.

    `np.ceil(matrix)` es la máscara de aristas: con pesos en [0, 1] solo se
    sortea donde hay arista, y una arista de peso p dispara con probabilidad p.
    """
    adj = np.ceil(matrix)
    probs = np.random.rand(*matrix.shape) * adj

    if directed == False:
        # Mismo sorteo en las dos direcciones de una arista no dirigida.
        upper = np.triu(probs)
        probs = upper + upper.T

    messages = (matrix > probs).astype(int).T

    return messages, probs


def propagacion(messages, new_values, values, node_ids, actualizo) -> list:
    """Un registro por mensaje enviado, en orden target y luego source.

    Cambio respecto a api.py: ahí la regla de `updated_value` estaba escrita
    para FV y AMP dentro del endpoint; aquí cada algoritmo trae la suya.
    """
    propagation_info = []
    for target_idx in range(len(messages)):
        for source_idx in range(len(messages[target_idx])):
            if messages[target_idx][source_idx] == 1:
                propagation_info.append({
                    "source": node_ids[source_idx],
                    "target": node_ids[target_idx],
                    "value_sent": float(values[source_idx]),
                    "updated_value": bool(actualizo(new_values, values, target_idx, source_idx)),
                })
    return propagation_info


def run_round(graph: dict, algorithm: str = "fv", params: dict | None = None) -> dict:
    """Una ronda sobre el grafo JSON del editor, con todo lo que pinta la interfaz.

    Cambio respecto a api.py: devuelve también los valores antes y después,
    las matrices sorteadas y la discrepancia, en tipos de Python (no NumPy)
    para que el Worker los mande como JSON.
    """
    algoritmo = buscar(algorithm)
    params = params or {}

    matrix = get_adj(graph)
    values = get_values(graph)
    node_ids = [node['id'] for node in graph['nodes']]

    messages, probs = who_gets_message(matrix, graph['directed'])
    new_values = algoritmo["ronda"](matrix, values, messages, params)
    actualizo = algoritmo.get("actualizo", cambio_el_valor)

    nuevos = [float(value) for value, _ in new_values]
    return {
        "values": nuevos,
        "valuesBefore": [float(value) for value in values],
        "selectedSources": [None if source is None else int(source) for _, source in new_values],
        "messages": messages.tolist(),
        "probabilities": probs.tolist(),
        "propagation": propagacion(messages, new_values, values, node_ids, actualizo),
        # Un grafo sin nodos no tiene rango: None en vez de error.
        "discrepancy": max(nuevos) - min(nuevos) if nuevos else None,
        "algorithm": algorithm,
    }


def update_graph_data(data: dict, new_values: list) -> dict:
    """
    Actualiza los valores del grafo con los nuevos valores.
    Lógica adaptada de update_json() en pruebas.ipynb
    """
    updated_data = deepcopy(data)

    for i, node in enumerate(updated_data['nodes']):
        # Cambio respecto a api.py: acepta valores sueltos además de tuplas (valor, emisor).
        value = new_values[i][0] if isinstance(new_values[i], (tuple, list)) else new_values[i]
        try:
            is_nan = np.isnan(value)
        except TypeError:
            is_nan = False
        node['value'] = float(value) if not is_nan else node['value']

    return updated_data


def simulate_graph(data: dict, algorithm: str = "fv", params: dict | None = None) -> dict:
    """La respuesta exacta del endpoint ``POST /simulate``: una ronda y el grafo nuevo."""
    result = run_round(data, algorithm, params)
    return {
        "success": True,
        "graph": update_graph_data(data, result["values"]),
        "propagation": result["propagation"],
        "algorithm": algorithm,
        "message": "Simulación completada exitosamente",
    }
