import pytest
from sentinel_ai.world.scenario import generate_scenario
from sentinel_ai.world.city_graph import build_city_graph

def test_scenario_generation():
    state1 = generate_scenario("TEST", 42, "mixed")
    state2 = generate_scenario("TEST", 42, "mixed")
    assert state1.timestamp_utc == state2.timestamp_utc
    assert len(state1.incidents) == len(state2.incidents)
    
def test_city_graph_generation():
    G1 = build_city_graph(42)
    G2 = build_city_graph(42)
    assert len(G1.nodes) == len(G2.nodes)
    assert len(G1.edges) == len(G2.edges)
