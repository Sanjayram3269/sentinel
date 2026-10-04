"""Road graph adapter and AI compatibility, without a database.

The persisted network is not required to prove the contract that matters
here: a graph built from road edges must expose exactly the attributes the AI
library subscripts, must carry real node coordinates, and must raise rather
than invent a position when a node has none.
"""

from __future__ import annotations

import networkx as nx
import pytest

from app.services.road_graph import (
    REQUIRED_EDGE_ATTRIBUTES,
    RoadGraphEdge,
    build_graph_from_edges,
    edge_attributes,
)


def make_edge(index: int, **overrides) -> RoadGraphEdge:
    defaults = {
        "road_edge_id": f"edge-{index}",
        "external_id": f"sumo:edge-{index}",
        "osm_way_id": 1000 + index,
        "from_node": f"n{index}",
        "to_node": f"n{index + 1}",
        "start_lon": 77.58 + index * 0.001,
        "start_lat": 13.09 + index * 0.001,
        "end_lon": 77.58 + (index + 1) * 0.001,
        "end_lat": 13.09 + (index + 1) * 0.001,
        "length_m": 120.0 + index,
        "speed_limit_kmh": 50.0,
        "road_class": "highway.primary",
        "lanes": 2,
        "has_signal": index == 0,
    }
    defaults.update(overrides)
    return RoadGraphEdge(**defaults)


def three_edge_graph() -> nx.DiGraph:
    return build_graph_from_edges([make_edge(i) for i in range(3)])


def test_graph_has_expected_shape() -> None:
    graph = three_edge_graph()
    assert graph.number_of_nodes() == 4
    assert graph.number_of_edges() == 3
    assert isinstance(graph, nx.DiGraph)


def test_every_required_attribute_is_published() -> None:
    graph = three_edge_graph()
    for _, _, data in graph.edges(data=True):
        for name in REQUIRED_EDGE_ATTRIBUTES:
            assert name in data, f"missing {name}"


def test_attributes_are_reachable_by_subscript_as_the_ai_library_does() -> None:
    graph = three_edge_graph()
    # Mirrors sentinel_ai.prediction.features, which subscripts rather than
    # calling .get, so an absent attribute is a KeyError rather than None.
    for _, _, data in graph.edges(data=True):
        assert isinstance(data["length_m"], float)
        assert isinstance(data["speed_limit_kmh"], float)
        assert isinstance(data["road_class"], str)
        assert isinstance(data["lanes"], int)
        assert isinstance(data["has_signal"], bool)


def test_identity_attributes_are_retained_for_the_routing_bridge() -> None:
    graph = three_edge_graph()
    data = graph["n0"]["n1"]
    assert data["road_edge_id"] == "edge-0"
    assert data["external_id"] == "sumo:edge-0"
    assert data["osm_way_id"] == 1000


def test_nodes_carry_real_coordinates() -> None:
    graph = three_edge_graph()
    for node in graph.nodes:
        assert "x" in graph.nodes[node]
        assert "y" in graph.nodes[node]
    assert graph.nodes["n0"]["x"] == pytest.approx(77.58)
    assert graph.nodes["n0"]["y"] == pytest.approx(13.09)


def test_supports_the_ai_routing_call() -> None:
    graph = three_edge_graph()
    path = nx.shortest_path(graph, source="n0", target="n3", weight="length_m")
    assert path == ["n0", "n1", "n2", "n3"]


def test_signal_flag_survives() -> None:
    graph = three_edge_graph()
    assert graph["n0"]["n1"]["has_signal"] is True
    assert graph["n1"]["n2"]["has_signal"] is False


def test_missing_node_raises_instead_of_guessing() -> None:
    graph = nx.DiGraph()
    graph.add_edge("a", "b", length_m=1.0)
    graph.add_node("a")
    graph.add_node("b")
    from sentinel_ai.world.geometry import node_position

    with pytest.raises(KeyError):
        node_position(graph, "a")
    with pytest.raises(KeyError):
        node_position(graph, "absent")


def test_edge_attributes_helper_is_stable() -> None:
    attributes = edge_attributes(make_edge(5))
    assert attributes["external_id"] == "sumo:edge-5"
    assert attributes["length_m"] == pytest.approx(125.0)
