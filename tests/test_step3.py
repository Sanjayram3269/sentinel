import pytest
from sentinel_ai.experiments.run_experiments import execute_baseline_B, execute_mission
from sentinel_ai.world.city_graph import build_city_graph
from sentinel_ai.world.scenario import generate_scenario

def test_sentinel_false_equals_B():
    G = build_city_graph(seed=42)
    seeds = list(range(150, 155)) # 5 seeds per scenario = 20
    scenarios = ["mixed", "accident", "hazard", "closure"]
    
    for s_type in scenarios:
        for seed in seeds:
            world = generate_scenario(f"TEST_{s_type}", seed, s_type)
            inc = world.incidents[0]
            req_type = inc.required_responder_types[0]
            unit = next((u for u in world.units if u.unit_type == req_type), None)
            
            eta_B, comp_B = execute_baseline_B(G, unit.position, inc.node, world.timestamp_utc, world, seed, 0, 0)
            eta_S, comp_S = execute_mission(G, unit.position, inc.node, world.timestamp_utc, world, seed, 0, 0, use_prediction=False)
            
            assert comp_B == comp_S
            if comp_B:
                assert abs(eta_B - eta_S) < 1e-4
            else:
                assert eta_B == float('inf') and eta_S == float('inf')
