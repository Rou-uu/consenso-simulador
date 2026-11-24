"""Topologías ya armadas: el catálogo ``TOPOLOGIAS`` y su paso al JSON del editor.

No importa ``js`` ni ``pyodide``: corre igual en Python de escritorio. En el
navegador, networkx lo baja bootstrap.js en el arranque, junto con NumPy.

**Agregar una topología es agregar una entrada a ``TOPOLOGIAS``**; el diálogo
«Topologías…» arma solo su botón, sus campos y su imagen. Cada entrada lleva:

``nombre``       el texto del botón.
``descripcion``  una o dos frases; se muestran junto a los parámetros.
``parametros``   ``{nombre: (etiqueta, valor_por_omision, minimo, maximo)}``. Si
                 el valor por omisión es entero, el parámetro es entero.
``grafo``        función que recibe los parámetros por nombre y devuelve un
                 ``Graph`` o ``DiGraph`` de networkx.
``acomodo``      (opcional) función ``grafo -> {nodo: (x, y)}``. Sin ella, o si
                 deja nodos encimados, se usa ``spring_layout`` y luego un círculo.
"""

from __future__ import annotations

import networkx as nx
import numpy as np

#: Más nodos que estos y el editor (SVG regenerado en cada evento) deja de responder.
MAX_NODOS = 200
#: En unidades del canvas: lo que mide un nodo y la separación típica entre vecinos.
DIAMETRO = 60.0
SEPARACION = 170.0


def _centro_y_anillo(grafo):
    """El nodo 0 al centro y el resto en círculo (estrella y rueda)."""
    return nx.shell_layout(grafo, [[0], list(grafo)[1:]])


TOPOLOGIAS = {
    "ciclo": {
        "nombre": "Ciclo",
        "descripcion": "Un anillo: cada nodo tiene exactamente dos vecinos. Un mensaje "
                       "necesita hasta n/2 saltos para llegar al lado opuesto.",
        "parametros": {"n": ("Nodos", 6, 3, MAX_NODOS)},
        "grafo": lambda n: nx.cycle_graph(n),
        "acomodo": nx.circular_layout,
    },
    "camino": {
        "nombre": "Camino",
        "descripcion": "Una fila de nodos: los extremos tienen un vecino y los demás, dos. "
                       "Es la gráfica conexa con las distancias más largas: n − 1 saltos de punta a punta.",
        "parametros": {"n": ("Nodos", 5, 2, MAX_NODOS)},
        "grafo": lambda n: nx.path_graph(n),
        "acomodo": lambda grafo: {nodo: (nodo, 0) for nodo in grafo},
    },
    "estrella": {
        "nombre": "Estrella",
        "descripcion": "Un centro conectado con todas las hojas; las hojas no se conectan "
                       "entre sí, así que todo mensaje pasa por el centro.",
        "parametros": {"hojas": ("Hojas", 6, 1, MAX_NODOS - 1)},
        "grafo": lambda hojas: nx.star_graph(hojas),
        "acomodo": _centro_y_anillo,
    },
    "completa": {
        "nombre": "Completa",
        "descripcion": "Todos conectados con todos: n(n − 1)/2 aristas. Cualquier mensaje "
                       "llega a cualquier nodo en un solo salto.",
        "parametros": {"n": ("Nodos", 5, 2, 60)},
        "grafo": lambda n: nx.complete_graph(n),
        "acomodo": nx.circular_layout,
    },
    "rueda": {
        "nombre": "Rueda",
        "descripcion": "Un ciclo más un centro conectado con todos los nodos del ciclo: "
                       "una estrella a la que se le unieron las hojas vecinas.",
        "parametros": {"n": ("Nodos (con el centro)", 7, 4, MAX_NODOS)},
        "grafo": lambda n: nx.wheel_graph(n),
        "acomodo": _centro_y_anillo,
    },
    "malla": {
        "nombre": "Malla",
        "descripcion": "Una cuadrícula: cada nodo se conecta con los de arriba, abajo, "
                       "izquierda y derecha. Los de la orilla tienen menos vecinos.",
        "parametros": {"filas": ("Filas", 3, 1, 14), "columnas": ("Columnas", 4, 1, 14)},
        "grafo": lambda filas, columnas: nx.grid_2d_graph(filas, columnas),
        "acomodo": lambda grafo: {(fila, columna): (columna, fila) for fila, columna in grafo},
    },
    "arbol": {
        "nombre": "Árbol",
        "descripcion": "Un árbol con raíz, llenado por niveles, donde cada nodo tiene a lo más "
                       "r hijos. No tiene ciclos: entre dos nodos hay un único camino.",
        "parametros": {"r": ("Hijos por nodo (r)", 2, 2, 6), "n": ("Nodos", 7, 1, MAX_NODOS)},
        "grafo": lambda r, n: nx.full_rary_tree(r, n),
        "acomodo": lambda grafo: nx.bfs_layout(grafo, 0, align="horizontal"),
    },
    "hipercubo": {
        "nombre": "Hipercubo",
        "descripcion": "2^d nodos, uno por cada cadena de d bits; dos son vecinos si difieren "
                       "en un solo bit. Cada nodo tiene d vecinos y la mayor distancia es d.",
        "parametros": {"d": ("Dimensión (d)", 3, 1, 7)},
        "grafo": lambda d: nx.hypercube_graph(d),
    },
    "bipartita": {
        "nombre": "Bipartita completa",
        "descripcion": "Dos grupos: cada nodo se conecta con todos los del otro grupo y con "
                       "ninguno del suyo.",
        "parametros": {"a": ("Nodos del primer grupo", 3, 1, 30), "b": ("Nodos del segundo grupo", 4, 1, 30)},
        "grafo": lambda a, b: nx.complete_bipartite_graph(a, b),
        "acomodo": lambda grafo: nx.bipartite_layout(
            grafo, [nodo for nodo, lado in grafo.nodes(data="bipartite") if lado == 0], align="horizontal"
        ),
    },
    "aleatoria": {
        "nombre": "Aleatoria",
        "descripcion": "Erdős–Rényi: cada par de nodos se conecta con probabilidad p, sin "
                       "depender de los demás. Puede quedar disconexa. La semilla fija el sorteo.",
        "parametros": {"n": ("Nodos", 10, 1, MAX_NODOS), "p": ("Probabilidad de arista (p)", 0.3, 0.0, 1.0),
                       "semilla": ("Semilla", 1, 0, 2**32 - 1)},
        "grafo": lambda n, p, semilla: nx.gnp_random_graph(n, p, seed=semilla),
    },
    "mundo_pequeno": {
        "nombre": "Mundo pequeño",
        "descripcion": "Watts–Strogatz: un anillo donde cada nodo se une a sus k vecinos más "
                       "cercanos y luego cada arista se reconecta al azar con probabilidad p. "
                       "Pocos atajos bastan para acortar mucho las distancias.",
        "parametros": {"n": ("Nodos", 12, 3, MAX_NODOS), "k": ("Vecinos por nodo (k)", 4, 2, 20),
                       "p": ("Probabilidad de reconectar (p)", 0.2, 0.0, 1.0),
                       "semilla": ("Semilla", 1, 0, 2**32 - 1)},
        "grafo": lambda n, k, p, semilla: nx.watts_strogatz_graph(n, k, p, seed=semilla),
        "acomodo": nx.circular_layout,
    },
}


def leer_parametros(clave: str, valores: dict | None = None) -> dict:
    """Los parámetros de ``clave`` como números, con lo de ``valores`` encima de los
    valores por omisión. ``ValueError`` si alguno no es un número o se sale de sus límites."""
    leidos = {}
    for nombre, (etiqueta, omision, minimo, maximo) in TOPOLOGIAS[clave]["parametros"].items():
        tipo = int if isinstance(omision, int) else float
        try:
            valor = tipo(str((valores or {}).get(nombre, omision)))
        except ValueError:
            raise ValueError(f"{etiqueta}: falta un número" + (" entero" if tipo is int else "")) from None
        if not minimo <= valor <= maximo:  # también descarta nan
            raise ValueError(f"{etiqueta}: debe estar entre {minimo} y {maximo}")
        leidos[nombre] = valor
    return leidos


def generar(clave: str, valores: dict | None = None) -> dict:
    """Una entrada de ``TOPOLOGIAS`` -> ``{directed, nodes, edges}`` con posiciones."""
    entrada = TOPOLOGIAS[clave]
    grafo = entrada["grafo"](**leer_parametros(clave, valores))
    if not 0 < len(grafo) <= MAX_NODOS:
        raise ValueError(f"la gráfica tendría {len(grafo)} nodos; se aceptan de 1 a {MAX_NODOS}")

    nodos = list(grafo.nodes)
    acomodos = [entrada["acomodo"]] if "acomodo" in entrada else []
    acomodos += [lambda g: nx.spring_layout(g, seed=0, iterations=500), nx.circular_layout]
    # Se prueba cada acomodo hasta que uno no encime nodos; el círculo nunca lo hace.
    # En cada intento se escala para que el nodo típico quede a SEPARACION de su vecino
    # más cercano. Con un solo nodo las cuentas dan nan y queda en el origen.
    with np.errstate(divide="ignore", invalid="ignore"):
        for acomodo in acomodos:
            posiciones = acomodo(grafo)
            puntos = np.array([posiciones[nodo] for nodo in nodos], dtype=float)
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

    def sin_encimar(datos: dict) -> bool:
        centros = np.array([[n["position"]["x"], n["position"]["y"]] for n in datos["nodes"]])
        lejos = np.linalg.norm(centros[:, None] - centros[None, :], axis=-1) + np.eye(len(centros)) * 1e9
        return bool(np.isfinite(centros).all() and lejos.min() >= DIAMETRO - 1e-6)

    for clave, entrada in TOPOLOGIAS.items():
        limites = entrada["parametros"]
        # Por omisión, todo al mínimo y todo al máximo (este puede pasarse de MAX_NODOS).
        for valores in ({}, {n: lim[2] for n, lim in limites.items()}, {n: lim[3] for n, lim in limites.items()}):
            try:
                datos = generar(clave, valores)
            except ValueError as error:
                assert valores and "nodos" in str(error), (clave, valores, error)
                continue
            grafo = entrada["grafo"](**leer_parametros(clave, valores))
            assert np.array_equal(get_adj(datos), nx.to_numpy_array(grafo)), (clave, valores)
            assert sin_encimar(datos), (clave, valores)
        primero = next(iter(limites))
        for malos in ({primero: "abc"}, {primero: limites[primero][3] + 1}):
            try:
                generar(clave, malos)
            except ValueError:
                continue
            raise AssertionError((clave, malos))
    # El texto de un campo llega como cadena, y un entero no acepta decimales.
    assert len(generar("ciclo", {"n": "8"})["nodes"]) == 8
    assert leer_parametros("aleatoria", {"p": "0.25"})["p"] == 0.25
    print(f"OK — topologias.py ({len(TOPOLOGIAS)} topologías)")
