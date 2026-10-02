import networkx as nx
import numpy as np
import math
from typing import List, Dict, Any, Tuple
from sentinel_ai.contracts import WorldState
from sentinel_ai.world.simulator import get_base_capacity, get_edge_delay_s

def compute_route_features(
    G: nx.DiGraph,
    route: List[str],
    depart_time_utc: float,
    world_state: WorldState
) -> Dict[str, float]:
    """Computes prediction features for a given route and state."""
    features = {}
    
    total_length = 0.0
    total_free_flow = 0.0
    num_signals = 0
    arterial_length = 0.0
    min_dist_incident = float('inf')
    hazard_overlap = 0.0
    closed_edge_flag = 0.0
    
    current_speeds = []
    occupancies = []
    
    for edge_id in route:
        u, v = edge_id.split("->")
        if edge_id in world_state.closed_edges:
            closed_edge_flag = 1.0
            
        data = G[u][v]
        length = data["length_m"]
        speed_limit_kmh = data["speed_limit_kmh"]
        speed_ms = speed_limit_kmh * 1000 / 3600
        free_flow_s = length / speed_ms
        
        total_length += length
        total_free_flow += free_flow_s / 60.0
        
        if data["has_signal"]:
            num_signals += 1
        if data["road_class"] == "arterial":
            arterial_length += length
            
        # Get simulated current condition (at depart time) to act as "current speed"
        tt_s, is_closed = get_edge_delay_s(G, u, v, depart_time_utc, world_state, seed=42)
        if is_closed:
            closed_edge_flag = 1.0
            current_speeds.append(0.0)
            occupancies.append(1.0)
        else:
            current_speed = (length / tt_s) * 3600 / 1000 if tt_s > 0 else speed_limit_kmh
            current_speeds.append(current_speed)
            
            capacity = get_base_capacity(data["lanes"], data["road_class"])
            volume = 500 * world_state.demand_level # Simplified for feature
            occupancies.append(min(1.0, volume / capacity))
            
        # Incident distance
        def get_pos(node_id):
            parts = node_id.split("_")
            return int(parts[1]) * 200, int(parts[2]) * 200
            
        ux, uy = get_pos(u)
        vx, vy = get_pos(v)
        mid_x, mid_y = (ux + vx) / 2.0, (uy + vy) / 2.0
        
        for inc in world_state.incidents:
            ix, iy = get_pos(inc.node)
            dist = math.hypot(mid_x - ix, mid_y - iy)
            min_dist_incident = min(min_dist_incident, dist)
            
        for haz in world_state.hazards:
            hx, hy = get_pos(haz.center_node)
            dist = math.hypot(mid_x - hx, mid_y - hy)
            if dist < haz.radius_m:
                hazard_overlap += length
                
    features["length"] = total_length
    features["free_flow_time"] = total_free_flow
    features["mean_current_speed"] = float(np.mean(current_speeds)) if current_speeds else 0.0
    features["min_current_speed"] = float(np.min(current_speeds)) if current_speeds else 0.0
    features["max_occupancy"] = float(np.max(occupancies)) if occupancies else 0.0
    features["num_signals"] = num_signals
    features["share_arterial"] = arterial_length / total_length if total_length > 0 else 0.0
    features["min_distance_incident"] = min_dist_incident if min_dist_incident != float('inf') else 10000.0
    
    hour = (depart_time_utc % 86400) / 3600.0
    features["time_of_day"] = hour
    features["demand_level"] = world_state.demand_level
    features["forecast_trend"] = 0.0 # Mocked as requested
    
    return features
