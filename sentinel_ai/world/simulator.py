import math
import numpy as np
import networkx as nx
from typing import List, Dict, Any, Tuple
from sentinel_ai.contracts import WorldState

# BPR function parameters
BPR_ALPHA = 0.15
BPR_BETA = 4.0

def get_base_capacity(lanes: int, road_class: str) -> float:
    # Vehicles per hour
    if road_class == "highway":
        return lanes * 2000.0
    elif road_class == "arterial":
        return lanes * 1000.0
    return lanes * 600.0

def time_of_day_multiplier(timestamp_utc: float) -> float:
    # Very simple rush hour multiplier based on hour of day
    hour = (timestamp_utc % 86400) / 3600.0
    if 7 <= hour <= 9 or 16 <= hour <= 18:
        return 1.5
    return 1.0

def get_edge_delay_s(
    G: nx.DiGraph,
    u: str,
    v: str,
    current_time: float,
    world_state: WorldState,
    seed: int
) -> Tuple[float, bool]:
    """Returns (travel_time_seconds, is_closed) for an edge."""
    edge_id = f"{u}->{v}"
    if edge_id in world_state.closed_edges:
        return float('inf'), True
        
    data = G[u][v]
    length_m = data["length_m"]
    speed_limit_kmh = data["speed_limit_kmh"]
    lanes = data["lanes"]
    road_class = data["road_class"]
    has_signal = data["has_signal"]
    
    speed_ms = speed_limit_kmh * 1000 / 3600
    free_flow_s = length_m / speed_ms
    
    # Calculate effective capacity
    capacity = get_base_capacity(lanes, road_class)
    
    # Reductions due to incidents (assuming they impact edges originating or ending at incident node)
    for incident in world_state.incidents:
        if incident.node == u or incident.node == v:
            capacity *= 0.5
            
    # Reductions due to hazards
    # Simplified: if edge nodes are within hazard radius, reduce capacity or close
    for hazard in world_state.hazards:
        # Distance calculation on grid is simplified: assume nodes are formatted as N_x_y and grid spacing is 200m
        def get_pos(node_id):
            parts = node_id.split("_")
            return int(parts[1]) * 200, int(parts[2]) * 200
        
        hx, hy = get_pos(hazard.center_node)
        ux, uy = get_pos(u)
        vx, vy = get_pos(v)
        
        # distance from edge midpoint to hazard center
        mid_x, mid_y = (ux + vx) / 2.0, (uy + vy) / 2.0
        dist = math.hypot(mid_x - hx, mid_y - hy)
        
        # Hazard radius at current_time
        time_elapsed = current_time - world_state.timestamp_utc
        current_radius = hazard.radius_m + max(0, time_elapsed) * hazard.expansion_rate_m_per_s
        
        if dist < current_radius:
            return float('inf'), True # Closed
        elif dist < current_radius + 500:
            capacity *= 0.2 # Severely slowed
            
    if capacity <= 0:
        return float('inf'), True
        
    # Calculate volume based on demand level and time of day
    volume = 500 * world_state.demand_level * time_of_day_multiplier(current_time)
    
    # BPR
    travel_time = free_flow_s * (1 + BPR_ALPHA * (volume / capacity) ** BPR_BETA)
    
    # Signal delay
    if has_signal:
        # Add average signal delay (15-45s)
        rng = np.random.RandomState(seed + int(current_time) % 1000)
        travel_time += rng.uniform(15, 45)
        
    # Seeded noise for congestion variance (± 10%)
    rng = np.random.RandomState(seed + int(travel_time * 10))
    travel_time *= rng.uniform(0.9, 1.1)
    
    # Emergency vehicle speed bonus (assuming 30% faster due to sirens)
    # but still bounded by free flow
    travel_time = max(free_flow_s * 0.8, travel_time * 0.7)
    
    return travel_time, False

def execute_route(
    G: nx.DiGraph,
    route: List[str],
    depart_time_utc: float,
    world_state: WorldState,
    seed: int = 42
) -> float:
    """Simulates moving through the route, returning the total actual travel time in minutes."""
    if not route:
        return 0.0
        
    current_time = depart_time_utc
    total_time_s = 0.0
    
    for edge_id in route:
        u, v = edge_id.split("->")
        tt_s, is_closed = get_edge_delay_s(G, u, v, current_time, world_state, seed)
        if is_closed:
            return float('inf') # Failed route
        total_time_s += tt_s
        current_time += tt_s
        
    return total_time_s / 60.0
