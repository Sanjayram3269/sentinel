import json
import os
import time

from sentinel_ai.world.city_graph import build_city_graph
from sentinel_ai.world.scenario import generate_scenario
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
from sentinel_ai.experiments.run_experiments import execute_mission, execute_baseline_B

def run_demo():
    print("--- SENTINEL: Geoagentic Framework to Support Emergency Movement ---")
    print("Loading models (if any)...")
    load_models()
    
    seed = 42
    print(f"\\n[Step 1] Initializing Synthetic City + Units + Hospitals + Incident (Seed {seed})...")
    world_state = generate_scenario("DEMO", seed, "mixed")
    incident = world_state.incidents[0]
    u_amb = next(u for u in world_state.units if u.unit_type == "ambulance")
    print(f"Incident {incident.incident_id} at {incident.node}. Requires: {incident.required_responder_types}")
    print(f"Ambulance {u_amb.unit_id} at {u_amb.position}.")
    
    print("\\n[Step 2 & 4] Generating Routes (Diverse k-shortest) + Roles + Scoring...")
    gen_req = GenerateRoutesRequest(
        origin=u_amb.position,
        destination=incident.node,
        depart_time_utc=world_state.timestamp_utc,
        world_state=world_state
    )
    routes_res = generate_and_score_routes(gen_req)
    for r in routes_res.routes[:3]:
        print(f"  Route {r.route_id} | Role: {r.role} | Score: {r.score:.2f} | P50 ETA: {r.eta_p50_min:.1f} min | Risk: {r.risk_probability*100:.1f}%")
        print(f"    Reason: {r.role_reason}")
    if routes_res.routes[0].risk_probability > 0.9:
        print("  [WARNING] ML Risk model predicts >90 percent failure on primary route. (Model limitation: overestimates risk on hazard proximity).")

    print("\\n[Step 3 & 5] Prediction & Resilience Computation...")
    res_req = ComputeResilienceRequest(
        routes=routes_res.routes,
        world_state=world_state
    )
    resilience = compute_resilience(res_req)
    print(f"  Resilience Score: {resilience.resilience_score:.1f}/100")
    for r in resilience.reasons:
        print(f"  - {r}")
        
    print("\\n[Step 6] Hospital Destination Ranking...")
    rank_req = RankDestinationsRequest(
        origin=incident.node,
        depart_time_utc=world_state.timestamp_utc + routes_res.routes[0].eta_p50_min * 60,
        world_state=world_state,
        required_capabilities=["trauma"]
    )
    hospitals = rank_destinations(rank_req)
    for h in hospitals.rankings:
        status = "Suitable" if h.suitable else "Unsuitable"
        eta_str = f"{h.predicted_eta_min:.1f} min" if h.predicted_eta_min is not None else "N/A"
        print(f"  Hospital {h.hospital_id} | {status} | Rank: {h.rank} | ETA (Incident->Hospital): {eta_str}")
        if not h.suitable:
            print(f"    Reasons: {h.unsuitable_reasons}")
        elif h.predicted_eta_min and h.predicted_eta_min > 50.0:
            print("  [WARNING] Hospital ETA is >50 min on small grid. (Model limitation: XGBoost OOD overestimation on long routes).")
            
    print("\\n[Step 7] Full Mission Optimizer (CP-SAT)...")
    opt_req = OptimizeMissionRequest(
        incident_id=incident.incident_id,
        world_state=world_state,
        available_unit_ids=[u.unit_id for u in world_state.units]
    )
    plan = optimize_mission(opt_req)
    print(f"  Expected Mission Time: {plan.expected_mission_time_min:.1f} min")
    for a in plan.assignments:
        # separate incident ETA and hospital ETA
        u = next(x for x in world_state.units if x.unit_id == a.unit_id)
        u_eta = generate_and_score_routes(GenerateRoutesRequest(origin=u.position, destination=incident.node, depart_time_utc=world_state.timestamp_utc, world_state=world_state)).routes[0].eta_p50_min
        if u.unit_type == "ambulance":
            print(f"  - Unit {a.unit_id} (Ambulance) -> Incident ETA: {u_eta:.1f} min, Hospital ETA: {a.expected_eta_min - u_eta:.1f} min (Total: {a.expected_eta_min:.1f} min)")
        else:
            print(f"  - Unit {a.unit_id} -> Incident ETA: {a.expected_eta_min:.1f} min")
        
    print("\\n[Step 8] What-If Scenario: Road Closure...")
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
    print(f"  Delta ETA: {cf_res.delta_eta_min:+.1f} min")
    if cf_res.delta_eta_min < 0:
        print("  [WARNING] What-if closure reduced predicted ETA. (Model limitation: XGBoost is non-monotonic and noisy).")
    
    print("\\n[Step 9 & 10] Operator Approval & Execution...")
    print("  (Auto-approved) Executing baseline vs SENTINEL plan in Simulator...")
    
    print("\\n[Step 11 & 12] Baseline vs SENTINEL Result (Real Execution)")
    G = build_city_graph(seed=42)
    eta_B, comp_B = execute_baseline_B(G, u_amb.position, incident.node, world_state.timestamp_utc, world_state, seed, 0, 0)
    eta_S, comp_S = execute_mission(G, u_amb.position, incident.node, world_state.timestamp_utc, world_state, seed, 0, 0, use_prediction=True)
    
    if comp_B: print(f"  Baseline B Mission Time: {eta_B:.1f} min")
    else: print("  Baseline B Mission Time: FAILED (inf)")
    
    if comp_S: print(f"  SENTINEL Mission Time:   {eta_S:.1f} min")
    else: print("  SENTINEL Mission Time:   FAILED (inf)")
    
    if comp_S and comp_B and eta_S > eta_B * 2:
        print("  [WARNING] XGBoost ETA > 2x Baseline actual. (Model limitation: scaling artifacts in synthetic training data).")

if __name__ == "__main__":
    run_demo()
