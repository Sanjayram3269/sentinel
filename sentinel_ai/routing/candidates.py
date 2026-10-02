import networkx as nx
from typing import List, Dict, Any, Tuple
from sentinel_ai.contracts import WorldState, RouteCandidate
from sentinel_ai.prediction.inference import predict_eta, predict_risk
from sentinel_ai.config_loader import config
from sentinel_ai.prediction.features import compute_route_features

def compute_overlap_fraction(route1: List[str], route2: List[str]) -> float:
    if not route1:
        return 0.0
    set1, set2 = set(route1), set(route2)
    return len(set1.intersection(set2)) / len(set1)

def edge_penalty_k_shortest_paths(G: nx.DiGraph, source: str, target: str, k: int, penalty_factor: float = 1.2) -> List[List[str]]:
    paths = []
    G_copy = G.copy()
    
    for _ in range(k):
        try:
            path_nodes = nx.shortest_path(G_copy, source=source, target=target, weight="length_m")
            path_edges = [f"{path_nodes[i]}->{path_nodes[i+1]}" for i in range(len(path_nodes)-1)]
            paths.append(path_edges)
            
            # Penalize edges
            for i in range(len(path_nodes)-1):
                u, v = path_nodes[i], path_nodes[i+1]
                G_copy[u][v]["length_m"] *= penalty_factor
        except nx.NetworkXNoPath:
            break
            
    return paths

def generate_and_score_routes(
    G: nx.DiGraph,
    origin: str,
    destination: str,
    depart_time_utc: float,
    world_state: WorldState
) -> List[RouteCandidate]:
    k = config["routing"]["k_shortest_paths"]
    
    # Temporarily remove closed edges from a copy of the graph to avoid generating paths through them
    G_valid = G.copy()
    for u, v in G.edges():
        edge_id = f"{u}->{v}"
        if edge_id in world_state.closed_edges:
            G_valid.remove_edge(u, v)
            
    raw_paths = []
    try:
        path_nodes = nx.shortest_path(G_valid, source=origin, target=destination, weight="length_m")
        raw_paths.append([f"{path_nodes[i]}->{path_nodes[i+1]}" for i in range(len(path_nodes)-1)])
    except nx.NetworkXNoPath:
        pass
    candidates = []
    weights = config["routing"]["score_weights"]
    
    for idx, path_edges in enumerate(raw_paths):
        # Predict ETAs and risk
        p10, p50, p90, baseline_eta, src, conf = predict_eta(G, path_edges, depart_time_utc, world_state)
        fails, prob, risk_src, risk_conf = predict_risk(G, path_edges, depart_time_utc, world_state)
        
        features = compute_route_features(G, path_edges, depart_time_utc, world_state)
        hazard_exposure = features.get("hazard_overlap", 0.0) / 1000.0 # roughly km of hazard
        
        # Calculate score (lower is better)
        score = (
            weights["w1_eta_p50"] * p50 +
            weights["w2_eta_p90_spread"] * (p90 - p50) +
            weights["w3_risk"] * prob * 100.0 + # Scale prob to comparable range
            weights["w4_hazard"] * hazard_exposure * 10.0
        )
        
        candidates.append(RouteCandidate(
            route_id=f"R_{idx}",
            edges=path_edges,
            eta_p10_min=p10,
            eta_p50_min=p50,
            eta_p90_min=p90,
            risk_probability=prob,
            score=score
        ))
        
    # Sort by score ascending (lower score is better)
    candidates.sort(key=lambda x: x.score)
    
    # Assign roles
    primary = None
    backup = None
    contingency = None
    
    backup_max_overlap = config["routing"]["backup_max_overlap_fraction"]
    contingency_max_overlap = config["routing"]["contingency_max_overlap_fraction"]
    
    for c in candidates:
        if primary is None:
            c.role = "primary"
            c.role_reason = "Best score"
            primary = c
        elif backup is None:
            overlap = compute_overlap_fraction(primary.edges, c.edges)
            if overlap <= backup_max_overlap:
                c.role = "backup"
                c.role_reason = f"Good score, overlap with primary {overlap:.2f} <= {backup_max_overlap}"
                backup = c
        elif contingency is None:
            overlap_p = compute_overlap_fraction(primary.edges, c.edges)
            overlap_b = compute_overlap_fraction(backup.edges, c.edges)
            if overlap_p <= contingency_max_overlap and overlap_b <= contingency_max_overlap:
                c.role = "contingency"
                c.role_reason = f"Acceptable score, diverse from primary ({overlap_p:.2f}) and backup ({overlap_b:.2f})"
                contingency = c
                
    return candidates
