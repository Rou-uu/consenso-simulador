"""Geometría y color del grafo SVG."""

from __future__ import annotations

import math
from typing import Iterable

from models import Edge, Node


NODE_RADIUS = 30.0
V0 = (110, 124, 242)
V1 = (244, 180, 92)


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def value_color(value: object) -> tuple[str, str]:
    try:
        amount = float(value)
        if math.isnan(amount):
            amount = 0.0
    except (TypeError, ValueError):
        amount = 0.0
    amount = clamp(amount, 0.0, 1.0)
    rgb = tuple(round(a + (b - a) * amount) for a, b in zip(V0, V1))
    luminance = (0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]) / 255
    text = "#1A1524" if luminance > 0.6 else "#F1EDF8"
    return f"rgb({rgb[0]}, {rgb[1]}, {rgb[2]})", text


def node_center(node: Node) -> tuple[float, float]:
    return node.x + NODE_RADIUS, node.y + NODE_RADIUS


def edge_geometry(source: Node, target: Node, curved: bool = False) -> dict[str, float | str]:
    sx, sy = node_center(source)
    tx, ty = node_center(target)
    dx, dy = tx - sx, ty - sy
    distance = math.hypot(dx, dy)

    if distance < 1e-9:
        # Self-loop: compact cubic loop above the node.
        start_x, start_y = sx - 18, sy - 24
        end_x, end_y = sx + 18, sy - 24
        path = f"M {start_x:.3f},{start_y:.3f} C {sx-48:.3f},{sy-78:.3f} {sx+48:.3f},{sy-78:.3f} {end_x:.3f},{end_y:.3f}"
        return {
            "path": path,
            "source_x": start_x,
            "source_y": start_y,
            "target_x": end_x,
            "target_y": end_y,
            "label_x": sx,
            "label_y": sy - 62,
        }

    ux, uy = dx / distance, dy / distance
    source_x, source_y = sx + ux * NODE_RADIUS, sy + uy * NODE_RADIUS
    target_x, target_y = tx - ux * NODE_RADIUS, ty - uy * NODE_RADIUS

    if curved:
        perp_x, perp_y = -uy, ux
        control_x = (source_x + target_x) / 2 + perp_x * 20
        control_y = (source_y + target_y) / 2 + perp_y * 20
        path = (
            f"M {source_x:.3f},{source_y:.3f} "
            f"Q {control_x:.3f},{control_y:.3f} {target_x:.3f},{target_y:.3f}"
        )
        label_x, label_y = control_x, control_y
    else:
        path = f"M {source_x:.3f},{source_y:.3f} L {target_x:.3f},{target_y:.3f}"
        label_x = (source_x + target_x) / 2
        label_y = (source_y + target_y) / 2

    return {
        "path": path,
        "source_x": source_x,
        "source_y": source_y,
        "target_x": target_x,
        "target_y": target_y,
        "label_x": label_x,
        "label_y": label_y,
    }


def has_reverse(edge: Edge, edges: Iterable[Edge]) -> bool:
    return any(
        other.id != edge.id
        and other.source == edge.target
        and other.target == edge.source
        for other in edges
    )


def fit_transform(nodes: list[Node], width: float, height: float) -> tuple[float, float, float]:
    if not nodes or width <= 0 or height <= 0:
        return 1.0, 0.0, 0.0
    min_x = min(node.x for node in nodes)
    min_y = min(node.y for node in nodes)
    max_x = max(node.x + NODE_RADIUS * 2 for node in nodes)
    max_y = max(node.y + NODE_RADIUS * 2 for node in nodes)
    graph_w = max(max_x - min_x, 1)
    graph_h = max(max_y - min_y, 1)
    padding = 80
    zoom = clamp(min((width - padding) / graph_w, (height - padding) / graph_h), 0.2, 2.0)
    pan_x = (width - graph_w * zoom) / 2 - min_x * zoom
    pan_y = (height - graph_h * zoom) / 2 - min_y * zoom
    return zoom, pan_x, pan_y
