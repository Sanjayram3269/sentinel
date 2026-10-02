from ortools.sat.python import cp_model
import networkx as nx
from typing import List, Dict, Any, Tuple
import time

from sentinel_ai.contracts import WorldState, Assignment, OptimizeMissionResponse
from sentinel_ai.routing.candidates import generate_and_score_routes
from sentinel_ai.hospital.ranking import rank_destinations
from sentinel_ai.config_loader import config

def build_travel_time_matrix(
    G: nx.DiGraph,
    units: List[Any],
    incident: Any,
    depart_time_utc: float,
    world_state: WorldState
) -> Tuple[Dict[str, float], Dict[str, Any]]:
    matrix = {}
    routes_cache = {}
    
    for u in units:
        routes = generate_and_score_routes(G, u.position, incident.node, depart_time_utc, world_state)
        primary = next((r for r in routes if r.role == "primary"), None)
        if primary:
            matrix[u.unit_id] = primary.eta_p50_min
            routes_cache[u.unit_id] = primary.edges
        else:
            matrix[u.unit_id] = float('inf')
            routes_cache[u.unit_id] = []
            
    return matrix, routes_cache

def greedy_mission_optimizer(
    G: nx.DiGraph,
    incident: Any,
    world_state: WorldState,
    available_units: List[Any],
    depart_time_utc: float
) -> OptimizeMissionResponse:
    matrix, routes_cache = build_travel_time_matrix(G, available_units, incident, depart_time_utc, world_state)
    
    assignments = []
    max_eta = 0.0
    covered_types = set()
    
    # Sort units by travel time
    sorted_units = sorted([u for u in available_units if matrix.get(u.unit_id, float('inf')) != float('inf')],
                          key=lambda u: matrix[u.unit_id])
                          
    for req_type in incident.required_responder_types:
        # Find best unit for this type
        best_u = next((u for u in sorted_units if u.unit_type == req_type and u.unit_id not in [a.unit_id for a in assignments]), None)
        if best_u:
            eta = matrix[best_u.unit_id]
            
            # If ambulance, also route to best hospital
            if best_u.unit_type == "ambulance":
                hospitals = rank_destinations(G, incident.node, depart_time_utc + eta * 60, world_state, ["trauma"])
                best_h = next((h for h in hospitals if h.suitable), None)
                if best_h:
                    eta += best_h.predicted_eta_min
                    
            assignments.append(Assignment(
                unit_id=best_u.unit_id,
                destination_node=incident.node,
                route_edges=routes_cache[best_u.unit_id],
                expected_eta_min=eta,
                is_hospital=False
            ))
            max_eta = max(max_eta, eta)
            
    return OptimizeMissionResponse(
        source="baseline_fallback",
        confidence=0.5,
        reasons=["Greedy heuristic used for mission optimization."],
        assignments=assignments,
        expected_mission_time_min=max_eta
    )

def optimize_mission(
    G: nx.DiGraph,
    incident_id: str,
    world_state: WorldState,
    available_unit_ids: List[str]
) -> OptimizeMissionResponse:
    start_time = time.time()
    time_limit = config["optimization"]["time_limit_seconds"]
    
    incident = next((i for i in world_state.incidents if i.incident_id == incident_id), None)
    if not incident:
        raise ValueError(f"Incident {incident_id} not found.")
        
    available_units = [u for u in world_state.units if u.unit_id in available_unit_ids and u.available]
    matrix, routes_cache = build_travel_time_matrix(G, available_units, incident, world_state.timestamp_utc, world_state)
    
    model = cp_model.CpModel()
    
    x = {} # x[u, i] = 1 if unit u is assigned to incident
    for u in available_units:
        x[u.unit_id] = model.NewBoolVar(f"x_{u.unit_id}")
        
    # Constraint: required types covered
    for req_type in incident.required_responder_types:
        model.Add(sum(x[u.unit_id] for u in available_units if u.unit_type == req_type) >= 1)
        
    # Constraint: max one unit per incident type (for simplicity here)
    for req_type in incident.required_responder_types:
        model.Add(sum(x[u.unit_id] for u in available_units if u.unit_type == req_type) <= 1)
        
    # Objective: minimize max travel time
    max_time = model.NewIntVar(0, 10000, "max_time")
    for u in available_units:
        if matrix[u.unit_id] != float('inf'):
            # Convert to int for CP-SAT (multiply by 10)
            t = int(matrix[u.unit_id] * 10)
            model.Add(max_time >= t * x[u.unit_id])
            
    model.Minimize(max_time)
    
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit
    status = solver.Solve(model)
    
    if status == cp_model.OPTIMAL or status == cp_model.FEASIBLE:
        assignments = []
        final_max_eta = 0.0
        
        for u in available_units:
            if solver.Value(x[u.unit_id]) == 1:
                eta = matrix[u.unit_id]
                dest_node = incident.node
                is_hospital = False
                
                # Ambulance post-incident routing
                if u.unit_type == "ambulance":
                    hospitals = rank_destinations(G, incident.node, world_state.timestamp_utc + eta * 60, world_state, ["trauma"])
                    best_h = next((h for h in hospitals if h.suitable), None)
                    if best_h:
                        eta += best_h.predicted_eta_min
                        
                assignments.append(Assignment(
                    unit_id=u.unit_id,
                    destination_node=dest_node,
                    route_edges=routes_cache[u.unit_id],
                    expected_eta_min=eta,
                    is_hospital=is_hospital
                ))
                final_max_eta = max(final_max_eta, eta)
                
        return OptimizeMissionResponse(
            source="ml",
            confidence=0.9,
            reasons=["CP-SAT optimizer found a plan.", f"Solver status: {solver.StatusName(status)}"],
            evidence={"solve_time_s": time.time() - start_time},
            assignments=assignments,
            expected_mission_time_min=final_max_eta
        )
        
    if config["optimization"]["greedy_fallback"]:
        return greedy_mission_optimizer(G, incident, world_state, available_units, world_state.timestamp_utc)
        
    return OptimizeMissionResponse(
        source="baseline_fallback",
        confidence=0.0,
        reasons=["Optimizer failed to find a plan and greedy fallback is disabled."],
        assignments=[],
        expected_mission_time_min=0.0
    )
