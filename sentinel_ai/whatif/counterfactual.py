import networkx as nx
from typing import List, Dict, Any, Tuple
import copy
import time

from sentinel_ai.contracts import (
    WorldState, OptimizeMissionRequest, CounterfactualChange,
    SimulateCounterfactualResponse, HazardState
)
from sentinel_ai.optimization.optimizer import optimize_mission
from sentinel_ai.routing.candidates import generate_and_score_routes
from sentinel_ai.routing.resilience import compute_resilience

def apply_change(state: WorldState, change: CounterfactualChange) -> WorldState:
    new_state = copy.deepcopy(state)
    
    if change.change_type == "close_edge":
        edge_id = change.change_data["edge_id"]
        if edge_id not in new_state.closed_edges:
            new_state.closed_edges.append(edge_id)
            
    elif change.change_type == "disable_hospital":
        hospital_id = change.change_data["hospital_id"]
        for h in new_state.hospitals:
            if h.hospital_id == hospital_id:
                h.available = False
                
    elif change.change_type == "increase_demand":
        new_state.demand_level *= change.change_data.get("multiplier", 1.5)
        
    elif change.change_type == "add_hazard":
        h = HazardState(
            hazard_id=change.change_data.get("hazard_id", "HAZ_NEW"),
            center_node=change.change_data["center_node"],
            radius_m=change.change_data.get("radius_m", 200.0),
            expansion_rate_m_per_s=change.change_data.get("expansion_rate_m_per_s", 0.5)
        )
        new_state.hazards.append(h)
        
    return new_state

def simulate_counterfactual(
    G: nx.DiGraph,
    state: WorldState,
    mission_request: OptimizeMissionRequest,
    change: CounterfactualChange,
    current_plan_eta: float,
    current_plan_resilience: float
) -> SimulateCounterfactualResponse:
    
    # 1. Apply change
    new_state = apply_change(state, change)
    
    # 2. Optimize new plan
    new_plan = optimize_mission(G, mission_request.incident_id, new_state, mission_request.available_unit_ids)
    
    # 3. Compute deltas
    delta_eta = new_plan.expected_mission_time_min - current_plan_eta
    route_changed = True # Assume changed if we re-optimized (can be refined)
    
    # Calculate new resilience
    incident = next((i for i in new_state.incidents if i.incident_id == mission_request.incident_id), None)
    new_resilience = 0.0
    if incident and new_plan.assignments:
        u_id = new_plan.assignments[0].unit_id
        u = next((unit for unit in new_state.units if unit.unit_id == u_id), None)
        if u:
            routes = generate_and_score_routes(G, u.position, incident.node, new_state.timestamp_utc, new_state)
            score, _, _ = compute_resilience(routes, new_state)
            new_resilience = score
            
    delta_resilience = new_resilience - current_plan_resilience
    
    return SimulateCounterfactualResponse(
        source="ml",
        confidence=0.9,
        reasons=["Counterfactual simulated successfully."],
        new_plan=new_plan,
        delta_eta_min=delta_eta,
        route_changed=route_changed,
        delta_resilience=delta_resilience
    )

def replan(
    G: nx.DiGraph,
    state: WorldState,
    event: CounterfactualChange,
    mission_request: OptimizeMissionRequest
) -> Tuple[OptimizeMissionResponse, float]:
    """Re-runs optimizer after a new event and reports latency."""
    start_time = time.time()
    
    new_state = apply_change(state, event)
    new_plan = optimize_mission(G, mission_request.incident_id, new_state, mission_request.available_unit_ids)
    
    latency = time.time() - start_time
    return new_plan, latency
