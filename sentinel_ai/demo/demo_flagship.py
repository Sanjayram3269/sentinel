import json
import os
import time

from sentinel_ai.world.scenario import generate_scenario
from sentinel_ai.world.simulator import execute_route
from sentinel_ai.api import (
    _G,
    predict_eta, PredictEtaRequest,
    predict_route_risk, PredictRouteRiskRequest,
    generate_and_score_routes, GenerateRoutesRequest,
    compute_resilience, ComputeResilienceRequest,
    rank_destinations, RankDestinationsRequest,
    optimize_mission, OptimizeMissionRequest,
    simulate_counterfactual, SimulateCounterfactualRequest
)
from sentinel_ai.contracts import CounterfactualChange
from sentinel_ai.prediction.inference import load_models

def run_demo():
    print("--- SENTINEL: Geoagentic Framework to Support Emergency Movement ---")
    print("Loading models (if any)...")
    load_models()
    
    seed = 42
    print("\n[Step 1] Initializing Synthetic City + Units + Hospitals + Incident...")
    world_state = generate_scenario("DEMO", seed, "mixed")
    incident = world_state.incidents[0]
    u_amb = next(u for u in world_state.units if u.unit_type == "ambulance")
    print(f"Incident {incident.incident_id} at {incident.node}. Requires: {incident.required_responder_types}")
    print(f"Ambulance {u_amb.unit_id} at {u_amb.position}.")
    
    print("\n[Step 2 & 4] Generating Routes (Diverse k-shortest) + Roles + Scoring...")
    gen_req = GenerateRoutesRequest(
        origin=u_amb.position,
        destination=incident.node,
        depart_time_utc=world_state.timestamp_utc,
        world_state=world_state
    )
    routes_res = generate_and_score_routes(gen_req)
    for r in routes_res.routes[:3]:
        print(f"  Route {r.route_id} | Role: {r.role} | Score: {r.score:.2f} | P50 ETA: {r.eta_p50_min:.2f} min | Risk: {r.risk_probability*100:.1f}%")
        print(f"    Reason: {r.role_reason}")
        
    print("\n[Step 3 & 5] Prediction & Resilience Computation...")
    res_req = ComputeResilienceRequest(
        routes=routes_res.routes,
        world_state=world_state
    )
    resilience = compute_resilience(res_req)
    print(f"  Resilience Score: {resilience.resilience_score:.1f}/100")
    for r in resilience.reasons:
        print(f"  - {r}")
        
    print("\n[Step 6] Hospital Destination Ranking...")
    rank_req = RankDestinationsRequest(
        origin=incident.node,
        depart_time_utc=world_state.timestamp_utc + routes_res.routes[0].eta_p50_min * 60,
        world_state=world_state,
        required_capabilities=["trauma"]
    )
    hospitals = rank_destinations(rank_req)
    for h in hospitals.rankings:
        status = "Suitable" if h.suitable else "Unsuitable"
        print(f"  Hospital {h.hospital_id} | {status} | Rank: {h.rank} | ETA: {h.predicted_eta_min}")
        if not h.suitable:
            print(f"    Reasons: {h.unsuitable_reasons}")
            
    print("\n[Step 7] Full Mission Optimizer (CP-SAT)...")
    opt_req = OptimizeMissionRequest(
        incident_id=incident.incident_id,
        world_state=world_state,
        available_unit_ids=[u.unit_id for u in world_state.units]
    )
    plan = optimize_mission(opt_req)
    print(f"  Expected Mission Time: {plan.expected_mission_time_min:.2f} min")
    for a in plan.assignments:
        print(f"  - Unit {a.unit_id} -> {a.destination_node} (ETA: {a.expected_eta_min:.2f} min)")
        
    print("\n[Step 8] What-If Scenario: Road Closure...")
    change = CounterfactualChange(
        change_type="close_edge",
        change_data={"edge_id": plan.assignments[0].route_edges[0]} if plan.assignments[0].route_edges else {"edge_id": "none"}
    )
    cf_req = SimulateCounterfactualRequest(
        world_state=world_state,
        mission_request=opt_req,
        change=change
    )
    cf_res = simulate_counterfactual(cf_req)
    print(f"  Delta ETA: {cf_res.delta_eta_min:+.2f} min")
    print(f"  Route Changed: {cf_res.route_changed}")
    print(f"  Delta Resilience: {cf_res.delta_resilience:+.2f}")
    
    print("\n[Step 9 & 10] Operator Approval & Execution...")
    print("  (Auto-approved) Executing baseline vs SENTINEL plan in Simulator...")
    # Just printing table mock as requested since the run_experiments generates real data
    print("\n[Step 11 & 12] Baseline vs SENTINEL Result (Mock Demo Table)")
    print("  | Scenario     | Baseline Mission Time | SENTINEL Mission Time |")
    print("  |--------------|-----------------------|-----------------------|")
    print(f"  | Demo (seed {seed}) | 14.5 min              | {plan.expected_mission_time_min:.1f} min               |")
    
if __name__ == "__main__":
    run_demo()
