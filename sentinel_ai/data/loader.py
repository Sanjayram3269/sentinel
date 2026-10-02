import pandas as pd
import numpy as np
import networkx as nx
import random
from typing import Tuple
from itertools import islice

from sentinel_ai.world.city_graph import build_city_graph
from sentinel_ai.world.scenario import generate_scenario
from sentinel_ai.world.simulator import execute_route
from sentinel_ai.prediction.features import compute_route_features
from sentinel_ai.prediction.baselines import baseline_free_flow_eta

def k_shortest_paths(G, source, target, k, weight='length_m'):
    try:
        return list(islice(nx.shortest_simple_paths(G, source, target, weight=weight), k))
    except nx.NetworkXNoPath:
        return []

def load_dataset(source: str = "synthetic", num_scenarios: int = 50, start_seed: int = 100) -> pd.DataFrame:
    """Loads dataset. If synthetic, generates it. If sumo, raises NotImplementedError."""
    if source == "sumo":
        raise NotImplementedError("SUMO data source not implemented yet. Expected columns: route_id, features..., actual_eta_min, route_fails")
        
    if source != "synthetic":
        raise ValueError("Invalid source. Must be 'synthetic' or 'sumo'.")
        
    G = build_city_graph(seed=42)
    nodes = list(G.nodes())
    data_rows = []
    
    for i in range(num_scenarios):
        seed = start_seed + i
        scenario_type = random.choice(["mixed", "accident", "hazard", "closure"])
        world_state = generate_scenario(f"TRAIN_{i}", seed, scenario_type)
        
        # Sample 10 random OD pairs
        random.seed(seed)
        for _ in range(10):
            u = random.choice(nodes)
            v = random.choice(nodes)
            if u == v:
                continue
                
            paths = k_shortest_paths(G, u, v, k=3)
            for path in paths:
                route_edges = [f"{path[j]}->{path[j+1]}" for j in range(len(path)-1)]
                
                features = compute_route_features(G, route_edges, world_state.timestamp_utc, world_state)
                ff_eta = baseline_free_flow_eta(G, route_edges)
                
                actual_eta = execute_route(G, route_edges, world_state.timestamp_utc, world_state, seed=seed)
                
                # Risk threshold from config (mocked as 1.5 here, but usually read from config)
                risk_threshold = 1.5
                route_fails = actual_eta == float('inf') or actual_eta > ff_eta * risk_threshold
                
                row = {
                    "scenario_id": f"TRAIN_{i}",
                    "scenario_type": scenario_type,
                    "seed": seed,
                    "actual_eta_min": min(actual_eta, 999.0) if actual_eta != float('inf') else 999.0,
                    "route_fails": int(route_fails),
                    "baseline_ff_eta": ff_eta,
                }
                row.update(features)
                data_rows.append(row)
                
    return pd.DataFrame(data_rows)
