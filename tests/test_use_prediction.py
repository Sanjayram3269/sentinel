import pytest
from sentinel_ai.experiments.run_experiments import execute_sentinel, execute_baseline_B
from sentinel_ai.world.scenario import generate_scenario
from sentinel_ai.world.city_graph import build_city_graph

def test_use_prediction_flag():
    import sentinel_ai.experiments.run_experiments as rx
    from sentinel_ai.config_loader import config
    
    # Force use_prediction = False
    config.setdefault("routing", {})["use_prediction"] = False
    
    # We test that execute_sentinel calls execute_mission with use_prediction=False
    world = generate_scenario("TEST_100", 100, "mixed")
    G = build_city_graph(seed=100)
    inc = world.incidents[0]
    u = next(x for x in world.units if x.unit_type == inc.required_responder_types[0])
    
    eta_S, comp_S = execute_sentinel(G, u.position, inc.node, world.timestamp_utc, world, 100, 0, 0.0)
    eta_B, comp_B = execute_baseline_B(G, u.position, inc.node, world.timestamp_utc, world, 100, 0, 0.0)
    
    # Since use_prediction is False, they must exactly match Baseline B
    assert comp_S == comp_B
    assert abs(eta_S - eta_B) < 1e-4
