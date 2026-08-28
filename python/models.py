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
    active_view: str = "editor"
    algorithm: str = "fv"
    # Valores de los parámetros por algoritmo: {"amp": {"agreed": 0.5}, ...}.
    # Se llena bajo demanda desde los defaults del registro, de modo que cambiar
    # de algoritmo y volver conserva lo que el usuario había escrito.
    algorithm_params: dict[str, dict[str, Any]] = field(default_factory=dict)
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

    @property
    def tab(self) -> GraphTab:
        for tab in self.tabs:
            if tab.id == self.active_tab_id:
                return tab
        self.active_tab_id = self.tabs[0].id
        return self.tabs[0]

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

