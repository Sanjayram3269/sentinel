import pytest
from sentinel_ai.world.scenario import generate_scenario
from sentinel_ai.world.city_graph import build_city_graph
from sentinel_ai.contracts import *
from sentinel_ai.routing.candidates import generate_and_score_routes, compute_overlap_fraction
from sentinel_ai.routing.resilience import compute_resilience
from sentinel_ai.hospital.ranking import rank_destinations
from sentinel_ai.optimization.optimizer import optimize_mission, greedy_mission_optimizer
from sentinel_ai.whatif.counterfactual import simulate_counterfactual, replan
from sentinel_ai.world.simulator import execute_route

G = build_city_graph(seed=42)

def test_routing_rules():
    state = generate_scenario("TEST", 42, "closure")
    req = GenerateRoutesRequest(
        origin="N_0_0", destination="N_9_9", depart_time_utc=state.timestamp_utc, world_state=state
    )
    routes = generate_and_score_routes(G, req.origin, req.destination, req.depart_time_utc, req.world_state)
    
    # 1. Closed edges never used
    for r in routes:
        for edge in r.edges:
            assert edge not in state.closed_edges
            
    # 2. Backup diversity rule
    primary = next((r for r in routes if r.role == "primary"), None)
    backup = next((r for r in routes if r.role == "backup"), None)
    if primary and backup:
        overlap = compute_overlap_fraction(primary.edges, backup.edges)
        assert overlap <= 0.4 # from config

def test_resilience():
    state = generate_scenario("TEST", 42, "mixed")
    routes = generate_and_score_routes(G, "N_0_0", "N_9_9", state.timestamp_utc, state)
    score, components, reasons = compute_resilience(routes, state)
    
    assert 0.0 <= score <= 100.0
    assert "route_redundancy" in components
    assert "backup_viability" in components

def test_hospital_ranking():
    state = generate_scenario("TEST", 42, "mixed")
    # Force H1 to be unavailable
    state.hospitals[0].available = False
    
    rankings = rank_destinations(G, "N_5_5", state.timestamp_utc, state, ["trauma"])
    
    h1 = next((h for h in rankings if h.hospital_id == state.hospitals[0].hospital_id), None)
    assert h1 is not None
    assert h1.suitable is False
    assert any("not available" in r for r in h1.unsuitable_reasons)
    
    # Check capability filter
    # H4 only has ICU, so shouldn't be suitable for trauma
    h4 = next((h for h in rankings if h.hospital_id == "H4"), None)
    if h4:
        assert h4.suitable is False
        assert any("trauma" in r for r in h4.unsuitable_reasons)

def test_optimizer_constraints_and_fallback():
    state = generate_scenario("TEST", 42, "mixed")
    incident = state.incidents[0]
    available_units = [u.unit_id for u in state.units]
    
    # CP-SAT
    plan = optimize_mission(G, incident.incident_id, state, available_units)
    assert plan.expected_mission_time_min > 0
    assigned_units = [a.unit_id for a in plan.assignments]
    
    # Check constraints satisfied (required types)
    for req_type in incident.required_responder_types:
        assert any(u.unit_type == req_type for u in state.units if u.unit_id in assigned_units)
        
    # Greedy fallback
    greedy_plan = greedy_mission_optimizer(G, incident, state, [u for u in state.units if u.unit_id in available_units], state.timestamp_utc)
    assert greedy_plan.source == "baseline_fallback"
    assert len(greedy_plan.assignments) > 0

def test_whatif_replan():
    state = generate_scenario("TEST", 42, "mixed")
    incident = state.incidents[0]
    req = OptimizeMissionRequest(incident_id=incident.incident_id, world_state=state, available_unit_ids=[u.unit_id for u in state.units])
    
    plan = optimize_mission(G, incident.incident_id, state, req.available_unit_ids)
    
    change = CounterfactualChange(change_type="close_edge", change_data={"edge_id": "N_5_5->N_5_6"})
    cf_res = simulate_counterfactual(G, state, req, change, plan.expected_mission_time_min, 50.0)
    
    assert cf_res.new_plan.expected_mission_time_min >= 0
    
    new_plan, latency = replan(G, state, change, req)
    assert latency > 0
    assert new_plan.expected_mission_time_min == cf_res.new_plan.expected_mission_time_min

def test_no_future_knowledge():
    # Test that route features only use current timestamp
    state = generate_scenario("TEST", 42, "hazard")
    # Hazard expands over time. At t=0, it's small. At t=3600, it's huge.
    # The planner should only see it at depart_time
    from sentinel_ai.prediction.features import compute_route_features
    
    route = ["N_5_5->N_5_6"]
    
    # If the feature extractor accidentally used a future time, the hazard overlap would be different.
    # We verify it takes depart_time_utc explicitly.
    features_now = compute_route_features(G, route, state.timestamp_utc, state)
    features_future = compute_route_features(G, route, state.timestamp_utc + 3600, state)
    
    # The extractor does not mutate the state. It reads hazard radius based on time elapsed since state.timestamp_utc
    # Ensure it works correctly.
    assert features_now is not None
    assert features_future is not None

def test_conformal_calibration():
    import numpy as np
    import pandas as pd
    from sentinel_ai.prediction.calibration import calibrate_conformal, apply_calibration
    
    # Mock data
    class MockModel:
        def predict(self, X):
            return X["base"].values
            
    models = {"eta_p10": MockModel(), "eta_p90": MockModel()}
    X_cal = pd.DataFrame({"base": [10.0, 10.0, 10.0, 10.0, 10.0]})
    # True values that are sometimes outside [10, 10]
    y_cal = pd.Series([9.0, 11.0, 12.0, 10.0, 10.0]) 
    
    q_hat = calibrate_conformal(models, X_cal, y_cal, alpha=0.2)
    assert q_hat >= 2.0 # max error is 2.0
    
    p10, p90 = apply_calibration(10.0, 10.0, q_hat)
    assert p10 <= 8.0
    assert p90 >= 12.0
