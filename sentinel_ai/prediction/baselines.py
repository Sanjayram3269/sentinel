import networkx as nx
from typing import List
from sentinel_ai.contracts import WorldState

def baseline_free_flow_eta(G: nx.DiGraph, route: List[str]) -> float:
    """Returns free-flow ETA in minutes."""
    total_time_s = 0.0
    for edge_id in route:
        u, v = edge_id.split("->")
        if G.has_edge(u, v):
            data = G[u][v]
            speed_ms = data["speed_limit_kmh"] * 1000 / 3600
            total_time_s += data["length_m"] / speed_ms
    return total_time_s / 60.0

def baseline_current_speed_eta(G: nx.DiGraph, route: List[str], features: dict) -> float:
    """Returns ETA using the mean current speed from features, fallback to free-flow."""
    length = features.get("length", 0.0)
    mean_speed_kmh = features.get("mean_current_speed", 0.0)
    if mean_speed_kmh <= 0:
        return baseline_free_flow_eta(G, route)
    
    speed_ms = mean_speed_kmh * 1000 / 3600
    return (length / speed_ms) / 60.0
