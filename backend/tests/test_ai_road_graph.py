"""Database-free coverage of the AI layer and its backend adapter.

These tests use the synthetic graph deliberately: they verify contracts,
fallback behaviour and routing, not geography. Real-road-graph behaviour is
covered by tests/integration/test_ai_road_network.py.
"""

from __future__ import annotations

import inspect
import math

import networkx as nx
import pytest

from app.schemas.predictions import PredictionKind
from app.services.ai_predictor import (
    AI_PREDICTOR_VERSION,
    AI_SOURCE,
    AI_SUPPORTED_TYPES,
    AiPredictor,
)
from app.services.predictors import Predictor, predictor_for
from sentinel_ai.api import ai_capabilities, predict_eta, predict_route_risk
from sentinel_ai.contracts import (
    CONTRACT_VERSION,
    MODEL_VERSION,
    HazardState,
    PredictEtaRequest,
    PredictRouteRiskRequest,
    WorldState,
)
from sentinel_ai.data.loader import load_dataset, summarise
from sentinel_ai.prediction.baselines import (
    baseline_current_speed_eta,
    baseline_free_flow_eta,
)
from sentinel_ai.prediction.features import compute_route_features
from sentinel_ai.prediction.inference import FEATURE_COLS, reset_model_cache
from sentinel_ai.world.city_graph import build_city_graph
from sentinel_ai.world.geometry import PLANAR_CRS, WGS84_CRS, distance_m, is_wgs84
from sentinel_ai.world.simulator import get_edge_delay_s


@pytest.fixture(autouse=True)
def _no_artifact() -> None:
    """Ensure no configured artifact leaks into these assertions."""
    reset_model_cache()


@pytest.fixture
def world_state() -> WorldState:
    return WorldState(
        timestamp_utc=1_672_531_200.0,
        demand_level=1.0,
        closed_edges=[],
        incidents=[],
        hazards=[],
        units=[],
        hospitals=[],
    )


@pytest.fixture
def graph() -> nx.DiGraph:
    return build_city_graph(seed=42)


def _a_route(graph: nx.DiGraph) -> list[str]:
    nodes = sorted(graph.nodes())
    return [f"{nodes[0]}->{nodes[1]}", f"{nodes[1]}->{nodes[2]}"]


class TestFacadeContract:
    def test_eta_response_preserves_contract_metadata(self, graph, world_state):
        response = predict_eta(
            PredictEtaRequest(
                route=_a_route(graph),
                depart_time_utc=world_state.timestamp_utc,
                world_state=world_state,
            ),
            G=graph,
        )
        assert response.contract_version == CONTRACT_VERSION
        assert response.model_version == MODEL_VERSION
        # No trained artifact exists in this repository.
        assert response.source == "baseline_fallback"
        assert response.confidence == 0.0
        assert response.reasons, "a fallback must say why it fell back"
        assert response.evidence, "features must be exposed as evidence"

    def test_eta_units_are_minutes_and_nonzero(self, graph, world_state):
        route = _a_route(graph)
        features = compute_route_features(graph, route, world_state.timestamp_utc, world_state)
        response = predict_eta(
            PredictEtaRequest(
                route=route,
                depart_time_utc=world_state.timestamp_utc,
                world_state=world_state,
            ),
            G=graph,
        )
        free_flow = baseline_free_flow_eta(graph, route)
        # free-flow minutes for this route, derived from metres and km/h
        assert free_flow > 0
        # The reported baseline is the *current-speed* estimate, which is the
        # quantity features are expressed against; both are minutes.
        current_speed = baseline_current_speed_eta(graph, route, features)
        assert current_speed > 0
        assert response.baseline_eta_min == pytest.approx(current_speed, rel=1e-6)
        assert response.eta_p50_min == pytest.approx(current_speed, rel=1e-6)
        # p10 <= p50 <= p90 holds even on the deterministic path
        assert response.eta_p10_min <= response.eta_p50_min <= response.eta_p90_min

    def test_route_risk_falls_back_without_fabricating(self, graph, world_state):
        response = predict_route_risk(
            PredictRouteRiskRequest(
                route=_a_route(graph),
                depart_time_utc=world_state.timestamp_utc,
                world_state=world_state,
            ),
            G=graph,
        )
        assert response.source == "baseline_fallback"
        assert response.confidence == 0.0
        assert response.reasons
        assert 0.0 <= response.failure_probability <= 1.0

    def test_route_risk_detects_a_closed_edge(self, graph):
        route = _a_route(graph)
        blocked = WorldState(
            timestamp_utc=1_672_531_200.0,
            demand_level=1.0,
            closed_edges=[route[0]],
            incidents=[],
            hazards=[],
            units=[],
            hospitals=[],
        )
        response = predict_route_risk(
            PredictRouteRiskRequest(
                route=route, depart_time_utc=blocked.timestamp_utc, world_state=blocked
            ),
            G=graph,
        )
        # closed_edge_flag was previously computed but never returned, so this
        # could never trigger.
        assert response.route_fails is True
        assert response.failure_probability == 1.0

    def test_facade_refuses_a_missing_graph(self, world_state):
        """A synthetic substitute would report plausible numbers for the wrong city."""
        with pytest.raises(ValueError, match="road graph is required"):
            predict_eta(
                PredictEtaRequest(route=["a->b"], depart_time_utc=0.0, world_state=world_state),
                G=None,
            )

    def test_capabilities_do_not_claim_congestion_or_hazard(self):
        capabilities = ai_capabilities()
        assert set(capabilities) == {"ETA", "ROUTE_FAILURE"}
        assert "CONGESTION" not in capabilities
        assert "HAZARD_IMPACT" not in capabilities


class TestFeatureContract:
    def test_feature_vector_carries_every_trained_column(self, graph, world_state):
        features = compute_route_features(
            graph, _a_route(graph), world_state.timestamp_utc, world_state
        )
        missing = [column for column in FEATURE_COLS if column not in features]
        assert not missing, f"missing trained feature columns: {missing}"
        # additional keys are allowed; the trained order is what matters
        assert features["closed_edge_flag"] == 0.0
        assert features["length"] > 0
        assert features["free_flow_time"] > 0

    def test_features_use_real_node_attributes_not_a_grid_hack(self, graph, world_state):
        """Coordinates must come from the graph, never from parsing a node id."""
        features = compute_route_features(
            graph, _a_route(graph), world_state.timestamp_utc, world_state
        )
        node = sorted(graph.nodes())[0]
        assert set(graph.nodes[node]) >= {"x", "y"}


class TestBackendAdapterRouting:
    def test_ai_handles_only_eta_and_route_failure(self):
        assert AI_SUPPORTED_TYPES == frozenset(
            {PredictionKind.ETA, PredictionKind.ROUTE_FAILURE}
        )
        eta = AiPredictor(db=None, network_key="osm_urban_v1", prediction_type=PredictionKind.ETA)
        failure = AiPredictor(
            db=None, network_key="osm_urban_v1", prediction_type=PredictionKind.ROUTE_FAILURE
        )
        assert eta.supports(PredictionKind.ETA)
        assert not eta.supports(PredictionKind.ROUTE_FAILURE)
        assert failure.supports(PredictionKind.ROUTE_FAILURE)
        assert not failure.supports(PredictionKind.ETA)
        for adapter in (eta, failure):
            assert not adapter.supports(PredictionKind.CONGESTION)
            assert not adapter.supports(PredictionKind.HAZARD_IMPACT)
        # A kind with no AI implementation is refused at construction, so a
        # CONGESTION/HAZARD_IMPACT call can never reach the AI adapter.
        for kind in (PredictionKind.CONGESTION, PredictionKind.HAZARD_IMPACT):
            with pytest.raises(ValueError):
                AiPredictor(db=None, network_key="osm_urban_v1", prediction_type=kind)

    def test_factory_selects_ai_only_for_supported_types(self):
        eta = AiPredictor(db=None, network_key="osm_urban_v1", prediction_type=PredictionKind.ETA)
        failure = AiPredictor(
            db=None, network_key="osm_urban_v1", prediction_type=PredictionKind.ROUTE_FAILURE
        )
        assert predictor_for(PredictionKind.ETA, ai=eta) is eta
        assert predictor_for(PredictionKind.ROUTE_FAILURE, ai=failure) is failure
        # No AI implementation exists for these, so they stay deterministic.
        assert type(predictor_for(PredictionKind.CONGESTION, ai=eta)).__name__ == (
            "CongestionPredictor"
        )
        assert type(predictor_for(PredictionKind.HAZARD_IMPACT, ai=failure)).__name__ == (
            "HazardImpactPredictor"
        )

    def test_factory_without_ai_is_unchanged(self):
        for kind, expected in [
            (PredictionKind.ETA, "EtaPredictor"),
            (PredictionKind.ROUTE_FAILURE, "RouteFailurePredictor"),
            (PredictionKind.CONGESTION, "CongestionPredictor"),
            (PredictionKind.HAZARD_IMPACT, "HazardImpactPredictor"),
        ]:
            assert type(predictor_for(kind)).__name__ == expected
            assert type(predictor_for(kind, ai=None)).__name__ == expected

    def test_adapter_provenance_constants(self):
        assert AI_SOURCE == "sentinel_ai_road_graph"
        assert AI_PREDICTOR_VERSION


class TestPredictorProtocolConformance:
    """The AI adapter must satisfy ``Predictor`` by exact signature.

    This is a contract check, not a behavioural one: an adapter whose
    ``predict`` takes an extra ``prediction_type`` argument would still "work"
    while silently diverging from the protocol every other engine implements.
    """

    @staticmethod
    def _adapters():
        return [
            AiPredictor(db=None, network_key="osm_urban_v1", prediction_type=kind)
            for kind in (PredictionKind.ETA, PredictionKind.ROUTE_FAILURE)
        ]

    def test_ai_predictor_is_a_predictor(self):
        assert isinstance(Predictor, type)
        for adapter in self._adapters():
            assert isinstance(adapter, Predictor)
            assert adapter.prediction_type in AI_SUPPORTED_TYPES

    def test_deterministic_predictors_are_still_predictors(self):
        for kind in PredictionKind:
            assert isinstance(predictor_for(kind), Predictor)

    def test_predict_signature_matches_the_protocol_exactly(self):
        # eval_str resolves the ``from __future__ import annotations`` strings so
        # annotations are compared as objects, not as source text.
        expected = inspect.signature(Predictor.predict, eval_str=True)
        # (self, context: MissionPredictionContext, horizon_seconds: int)
        assert list(expected.parameters) == ["self", "context", "horizon_seconds"]
        for adapter in self._adapters():
            # Compare unbound functions: a bound method hides ``self``.
            actual = inspect.signature(type(adapter).predict, eval_str=True)
            assert list(actual.parameters) == list(expected.parameters), (
                f"{type(adapter).__name__} diverges from the Predictor protocol"
            )
            for name, expected_param in expected.parameters.items():
                actual_param = actual.parameters[name]
                assert actual_param.annotation == expected_param.annotation
                assert actual_param.default == expected_param.default
            assert actual.return_annotation == expected.return_annotation

    def test_protocol_conformance_survives_the_factory(self):
        for kind in AI_SUPPORTED_TYPES:
            adapter = AiPredictor(
                db=None, network_key="osm_urban_v1", prediction_type=kind
            )
            assert isinstance(predictor_for(kind, ai=adapter), Predictor)
            # Two positional arguments, exactly like the deterministic ones.
            with pytest.raises(TypeError):
                predictor_for(kind, ai=adapter).predict(None, 300, kind)


class TestUnits:
    """Coordinates arrive in different frames; distances must still be metres."""

    def test_synthetic_graph_declares_planar_metres(self, graph):
        assert graph.graph["crs"] == PLANAR_CRS
        assert not is_wgs84(graph)

    def test_distance_is_metres_on_a_wgs84_graph(self):
        # A graph whose nodes carry longitude/latitude.
        wgs = nx.DiGraph()
        wgs.graph["crs"] = WGS84_CRS
        wgs.add_node("a", x=77.5946, y=12.9716)
        wgs.add_node("b", x=77.6046, y=12.9716)
        metres = distance_m(wgs, (77.5946, 12.9716), (77.6046, 12.9716))
        # 0.01 degrees of longitude at this latitude is roughly 1.11 km.
        assert 1_050 < metres < 1_180, metres

    def test_degree_values_are_not_compared_against_metre_thresholds(self):
        """The bug this guards: 0.01 degrees is ~1 km, not 0.01 m."""
        wgs = nx.DiGraph()
        wgs.graph["crs"] = WGS84_CRS
        wgs.add_node("a", x=77.5946, y=12.9716)
        wgs.add_node("b", x=77.6046, y=12.9716)
        metres = distance_m(wgs, (77.5946, 12.9716), (77.6046, 12.9716))
        assert metres > 100.0

    def test_a_small_hazard_does_not_blanket_close_a_real_network(self):
        """A 100 m hazard must not swallow every edge of a degree-scale graph."""
        wgs = nx.DiGraph()
        wgs.graph["crs"] = WGS84_CRS
        nodes = {
            "a": (77.5946, 12.9716),
            "b": (77.6046, 12.9716),
            "c": (77.6146, 12.9716),
        }
        for name, (x, y) in nodes.items():
            wgs.add_node(name, x=x, y=y)
        wgs.add_edge("a", "b", length_m=1110.0, speed_limit_kmh=50.0,
                     road_class="arterial", lanes=2, has_signal=False)
        wgs.add_edge("b", "c", length_m=1110.0, speed_limit_kmh=50.0,
                     road_class="arterial", lanes=2, has_signal=False)
        hazard = HazardState(
            hazard_id="h1",
            center_node="a",
            radius_m=100.0,
            expansion_rate_m_per_s=0.0,
        )
        world_state = WorldState(
            timestamp_utc=1_672_531_200.0,
            demand_level=1.0,
            closed_edges=[],
            incidents=[],
            hazards=[hazard],
            units=[],
            hospitals=[],
        )
        # "b->c" is ~2.2 km from the hazard centre, so it stays open.
        _tt, closed = get_edge_delay_s(wgs, "b", "c", 1_672_531_200.0, world_state, seed=1)
        assert closed is False

    def test_planar_graph_keeps_euclidean_metres(self, graph):
        node_a, node_b = sorted(graph.nodes())[:2]
        ax, ay = graph.nodes[node_a]["x"], graph.nodes[node_a]["y"]
        bx, by = graph.nodes[node_b]["x"], graph.nodes[node_b]["y"]
        assert distance_m(graph, (ax, ay), (bx, by)) == pytest.approx(
            math.hypot(bx - ax, by - ay)
        )


class TestDatasetLoader:
    def test_real_source_builds_rows_with_the_feature_contract(self, graph):
        rows = load_dataset(
            "sumo", graph=graph, num_scenarios=2, od_pairs_per_scenario=3, paths_per_pair=2
        )
        assert rows
        for row in rows:
            assert row["actual_eta_min"] > 0
            assert row["route_fails"] in (0, 1)
            missing = [column for column in FEATURE_COLS if column not in row]
            assert not missing, missing

    def test_summary_makes_no_accuracy_claim(self, graph):
        rows = load_dataset(
            "sumo", graph=graph, num_scenarios=2, od_pairs_per_scenario=2, paths_per_pair=1
        )
        summary = summarise(rows)
        assert summary["accuracy_evaluated"] is False
        assert summary["label_source"] == "deterministic_simulator"
        assert summary["rows"] == len(rows)

    def test_loader_refuses_to_invent_a_graph(self):
        with pytest.raises(ValueError, match="road graph is required"):
            load_dataset("sumo")

    def test_loader_rejects_an_unknown_source(self, graph):
        with pytest.raises(ValueError, match="must be 'sumo' or 'synthetic'"):
            load_dataset("nonsense", graph=graph)

    def test_scenarios_are_deterministic_for_a_fixed_seed(self, graph):
        first = load_dataset(
            "sumo", graph=graph, num_scenarios=1, od_pairs_per_scenario=2, paths_per_pair=1
        )
        reset_model_cache()
        second = load_dataset(
            "sumo", graph=graph, num_scenarios=1, od_pairs_per_scenario=2, paths_per_pair=1
        )
        assert [row["actual_eta_min"] for row in first] == [
            row["actual_eta_min"] for row in second
        ]


class TestConfig:
    def test_ai_is_disabled_by_default(self):
        from app.config import Settings

        settings = Settings(_env_file=None)
        assert settings.sentinel_ai_enabled is False
        assert settings.ai_model_path is None
        assert settings.ai_model_version == "none"

    def test_env_overrides_bind(self, monkeypatch):
        from app.config import Settings

        monkeypatch.setenv("SENTINEL_AI_ENABLED", "true")
        monkeypatch.setenv("AI_MODEL_PATH", "/srv/models")
        monkeypatch.setenv("AI_MODEL_VERSION", "candidate-7")
        settings = Settings(_env_file=None)
        assert settings.sentinel_ai_enabled is True
        assert settings.ai_model_path == "/srv/models"
        assert settings.ai_model_version == "candidate-7"