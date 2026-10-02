import networkx as nx
import numpy as np
import random
from typing import Tuple

def build_city_graph(seed: int = 42) -> nx.DiGraph:
    """Builds a synthetic city graph (perturbed grid + arterials + ring road)."""
    np.random.seed(seed)
    random.seed(seed)
    
    # 10x10 grid
    G = nx.grid_2d_graph(10, 10, create_using=nx.DiGraph)
    
    # Add reverse edges to make it a fully bidirectional grid
    reverse_edges = [(v, u) for u, v in G.edges()]
    G.add_edges_from(reverse_edges)
    
    # Map (i,j) nodes to string IDs
    mapping = {node: f"N_{node[0]}_{node[1]}" for node in G.nodes()}
    nx.relabel_nodes(G, mapping, copy=False)
    
    # Add attributes to edges
    for u, v, data in G.edges(data=True):
        data["edge_id"] = f"{u}->{v}"
        
        # Base grid edges are local roads
        data["road_class"] = "local"
        data["speed_limit_kmh"] = 30.0
        data["lanes"] = 1
        data["length_m"] = float(np.random.uniform(150, 250))
        data["has_signal"] = False
        
        # Some are arterials (e.g., column 3 and 7, row 3 and 7)
        u_parts = u.split("_")
        v_parts = v.split("_")
        u_x, u_y = int(u_parts[1]), int(u_parts[2])
        v_x, v_y = int(v_parts[1]), int(v_parts[2])
        
        is_arterial = (u_x in (3, 7) and v_x in (3, 7)) or (u_y in (3, 7) and v_y in (3, 7))
        
        if is_arterial:
            data["road_class"] = "arterial"
            data["speed_limit_kmh"] = 50.0
            data["lanes"] = 2
            data["has_signal"] = True
            
        # Ring road on the perimeter
        is_ring = (u_x in (0, 9) and v_x in (0, 9)) or (u_y in (0, 9) and v_y in (0, 9))
        if is_ring:
            data["road_class"] = "highway"
            data["speed_limit_kmh"] = 80.0
            data["lanes"] = 3
            data["has_signal"] = False

    return G

def get_free_flow_time_s(length_m: float, speed_limit_kmh: float) -> float:
    speed_ms = speed_limit_kmh * 1000 / 3600
    return length_m / speed_ms
