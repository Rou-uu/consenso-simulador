"""Modelos de estado del editor, independientes del DOM y de Pyodide."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Node:
    id: str
    value: int | float = 0
    x: float = 0.0
    y: float = 0.0

    @classmethod
    def from_json(cls, data: dict[str, Any], fallback_x: float = 0, fallback_y: float = 0) -> "Node":
        position = data.get("position") or {}
        value = data.get("value", 0)
        return cls(
            id=str(data["id"]),
            value=0 if value is None else value,
            x=float(position.get("x", fallback_x)),
            y=float(position.get("y", fallback_y)),
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "value": self.value,
            "position": {"x": self.x, "y": self.y},
        }


@dataclass
class Edge:
    id: str
    source: str
    target: str
    weight: str = "1"
    highlight: str | None = None  # updated | delivered | quiet | None

    @classmethod
    def from_json(cls, data: dict[str, Any], index: int) -> "Edge":
        weight = data.get("weight", "1")
        return cls(
            id=f"edge_{index}",
            source=str(data["source"]),
            target=str(data["target"]),
            weight="1" if weight is None else str(weight),
        )

    def to_json(self, index: int) -> dict[str, Any]:
        return {
            "id": f"edge_{index}",
            "source": self.source,
            "target": self.target,
            "weight": self.weight,
        }


@dataclass
class GraphTab:
    id: str
    name: str
    nodes: list[Node] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    directed: bool = True

    def graph_data(self) -> dict[str, Any]:
        return {
            "directed": self.directed,
            "nodes": [node.to_json() for node in self.nodes],
            "edges": [edge.to_json(index) for index, edge in enumerate(self.edges)],
        }

    def clone(self) -> "GraphTab":
        return deepcopy(self)


@dataclass
class SimulationState:
    before: GraphTab
    after: GraphTab
    propagation: list[dict[str, Any]]


@dataclass
class AppState:
    tabs: list[GraphTab] = field(default_factory=lambda: [GraphTab("tab_0", "Grafo 1")])
    active_tab_id: str = "tab_0"
    active_view: str = "analysis"
    # Clave de TEMAS (app.py): "cafe" o "claro".
    theme: str = "cafe"
    algorithm: str = "fv"
    # Valores de los parámetros por algoritmo: {"amp": {"agreed": 0.5}, ...}.
    # Se llena bajo demanda desde los defaults de ALGORITMOS, de modo que cambiar
    # de algoritmo y volver conserva lo que el usuario había escrito.
    algorithm_params: dict[str, dict[str, Any]] = field(default_factory=dict)
    # Paso de mensajes: "por_arista" o "por_emisor" (broadcast).
    message_mode: str = "por_arista"
    show_json: bool = False
    shift_pressed: bool = False
    space_pressed: bool = False
    show_after: bool = False
    initial_graph: GraphTab | None = None
    simulation: SimulationState | None = None
    selected_node_id: str | None = None
    selected_edge_id: str | None = None
    zoom: float = 1.0
    pan_x: float = 0.0
    pan_y: float = 0.0
    node_counter: int = 0
    tab_counter: int = 1
    sweep_running: bool = False
    # La semilla con la que corrió (o corre) el último sweep: la escrita o la sorteada.
    sweep_seed: int | None = None
    # Los algoritmos palomeados para el sweep (el editor y «Código» usan `algorithm`).
    sweep_algorithms: list[str] = field(default_factory=lambda: ["fv"])
    # El último sweep, un experimento por algoritmo: lo que se mandó a su Worker y
    # la curva que va devolviendo. Las descargas salen de aquí, no de los
    # controles, que pudieron cambiar después.
    experiments: dict[str, dict[str, Any]] = field(default_factory=dict)
    # De cuál de ellos son la tabla, el desglose y las descargas CSV/JSON.
    detail_algorithm: str | None = None
    # Curvas despalomeadas en «Mostrar en la gráfica»: "fv:sim", "fv:teo" o "ic".
    hidden_curves: set[str] = field(default_factory=set)
    # El worker avisa con un mensaje `listo` cuando termino de cargar Pyodide y
    # NumPy. Hasta entonces una ronda pedida espera dentro del worker, y el boton
    # lo dice en vez de fingir que ya esta calculando.
    engine_ready: bool = False
    # La ronda se calcula en el worker, asi que `simulate` termina antes de que
    # llegue el resultado. Lo que antes era una variable local viviendo en la
    # pila durante toda la llamada tiene que esperar aqui.
    round_running: bool = False
    round_before: GraphTab | None = None
    round_tab_id: str | None = None
    # El codigo pegado en la ranura de usuario. Viaja con cada mensaje al
    # Worker: asi el Worker no tiene estado que sincronizar y recrearlo tras un
    # plazo vencido no obliga a reinstalar nada.
    user_source: str = ""

    @property
    def tab(self) -> GraphTab:
        for tab in self.tabs:
            if tab.id == self.active_tab_id:
                return tab
        self.active_tab_id = self.tabs[0].id
        return self.tabs[0]

    @property
    def experiment(self) -> dict[str, Any] | None:
        """El experimento en detalle (tabla, desglose, descargas)."""
        return self.experiments.get(self.detail_algorithm)

    def next_node_id(self) -> str:
        value = f"node_{self.node_counter}"
        self.node_counter += 1
        return value

    def next_tab_id(self) -> str:
        value = f"tab_{self.tab_counter}"
        self.tab_counter += 1
        return value

    def capture_initial(self) -> None:
        if self.simulation is None and self.tab.nodes:
            self.initial_graph = self.tab.clone()

    def clear_simulation(self) -> None:
        self.initial_graph = None
        self.simulation = None
        self.show_after = False

