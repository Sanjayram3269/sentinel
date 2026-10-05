"""Deterministic ETA baselines.

These are the reference values every learned model is expressed relative to.
They depend only on stored road attributes, so they remain available when no
trained artifact is present and are the documented fallback source.
"""

import networkx as nx
from typing import Dict, List

__all__ = [
    "baseline_free_flow_eta",
    "baseline_current_speed_eta",
]


def baseline_free_flow_eta(G: nx.DiGraph, route: List[str]) -> float:
    """Return free-flow ETA in minutes.

    Units follow the contract: ``length_m`` is metres, ``speed_limit_kmh`` is
    kilometres per hour, and the result is minutes.
    """
    total_time_s = 0.0
    for edge_id in route:
        u, v = edge_id.split("->")
        if G.has_edge(u, v):
            data = G[u][v]
            speed_ms = data["speed_limit_kmh"] * 1000 / 3600
            total_time_s += data["length_m"] / speed_ms
    return total_time_s / 60.0


def baseline_current_speed_eta(
    G: nx.DiGraph, route: List[str], features: Dict[str, float]
) -> float:
    """Return ETA in minutes using mean observed speed, else free-flow."""
    length = features.get("length", 0.0)
    mean_speed_kmh = features.get("mean_current_speed", 0.0)
    if mean_speed_kmh <= 0:
        return baseline_free_flow_eta(G, route)
    speed_ms = mean_speed_kmh * 1000 / 3600
    return (length / speed_ms) / 60.0