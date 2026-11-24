"""Topologías ya armadas: un generador de networkx -> el JSON del editor.

No importa ``js`` ni ``pyodide``: corre igual en Python de escritorio. En el
navegador, networkx lo baja bootstrap.js en el arranque, junto con NumPy.
"""

from __future__ import annotations

import networkx as nx
import numpy as np

#: Más nodos que estos y el editor (SVG regenerado en cada evento) deja de responder.
MAX_NODOS = 200
#: En unidades del canvas: lo que mide un nodo y la separación típica entre vecinos.
DIAMETRO = 60.0
SEPARACION = 170.0


def generar(expresion: str) -> dict:
    """``"cycle_graph(6)"`` -> ``{directed, nodes, edges}`` con posiciones."""
    # shortcut: eval en el hilo principal, sin plazo; una expresión enorme
    # (`hypercube_graph(25)`) congela la pestaña. Si estorba, moverlo al Worker.
    grafo = eval(expresion, {"nx": nx, **vars(nx)})
    if not isinstance(grafo, nx.Graph) or grafo.is_multigraph():
        raise ValueError("la expresión debe dar un Graph o DiGraph de networkx")
    if not 0 < len(grafo) <= MAX_NODOS:
        raise ValueError(f"la gráfica tiene {len(grafo)} nodos; se aceptan de 1 a {MAX_NODOS}")

    nodos = list(grafo.nodes)
    # Acomodo por fuerzas; si deja nodos encimados (pasa con gráficas densas), en círculo.
    # En cada intento se escala para que el nodo típico quede a SEPARACION de su vecino
    # más cercano. Con un solo nodo las cuentas dan nan y queda en el origen.
    with np.errstate(divide="ignore", invalid="ignore"):
        for acomodo in (nx.spring_layout(grafo, seed=0, iterations=500), nx.circular_layout(grafo)):
            puntos = np.array([acomodo[nodo] for nodo in nodos], dtype=float)
            distancias = np.linalg.norm(puntos[:, None] - puntos[None, :], axis=-1)
            np.fill_diagonal(distancias, np.inf)
            cercanas = distancias.min(axis=1)
            puntos *= SEPARACION / np.median(cercanas)
            if cercanas.min() * SEPARACION / np.median(cercanas) >= DIAMETRO:
                break

    ids = {nodo: f"node_{i}" for i, nodo in enumerate(nodos)}
    return {
        "directed": grafo.is_directed(),
        "nodes": [
            {"id": ids[nodo], "value": 0, "position": {"x": float(x), "y": float(y)}}
            for nodo, (x, y) in zip(nodos, puntos)
        ],
        "edges": [
            {"id": f"edge_{k}", "source": ids[u], "target": ids[v], "weight": str(peso)}
            # El editor no dibuja lazos (una arista de un nodo a sí mismo).
            for k, (u, v, peso) in enumerate(grafo.edges(data="weight", default=1))
            if u != v
        ],
    }


if __name__ == "__main__":
    from simulation.ronda import get_adj

    for caso in ("cycle_graph(6)", "nx.star_graph(5)", "grid_2d_graph(3, 3)", "path_graph(1)",
                 "complete_graph(4, create_using=DiGraph)", "erdos_renyi_graph(30, 0.2, seed=1)",
                 "grid_2d_graph(5, 5)", f"complete_graph({MAX_NODOS})"):
        datos, grafo = generar(caso), eval(caso, {"nx": nx, **vars(nx)})
        assert np.array_equal(get_adj(datos), nx.to_numpy_array(grafo)), caso
        centros = np.array([[n["position"]["x"], n["position"]["y"]] for n in datos["nodes"]])
        lejos = np.linalg.norm(centros[:, None] - centros[None, :], axis=-1) + np.eye(len(centros)) * 1e9
        assert np.isfinite(centros).all() and lejos.min() >= DIAMETRO - 1e-6, (caso, lejos.min())
    for mala in ("5", "MultiGraph([(0, 1)])", "empty_graph(0)", f"path_graph({MAX_NODOS + 1})"):
        try:
            generar(mala)
        except ValueError:
            continue
        raise AssertionError(mala)
    print("OK — topologias.py")
