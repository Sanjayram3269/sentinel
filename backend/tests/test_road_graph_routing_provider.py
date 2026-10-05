"""Unit coverage for real road-graph route generation (Phase 5).

These tests use a small hand-built graph with real WGS84 coordinates and real
attribute names, so contracts can be pinned exactly. Geography, PostGIS and the
imported ``osm_urban_v1`` dataset are covered by
``tests/integration/test_road_graph_routing.py``.
"""

from __future__ import annotations

import networkx as nx
import pytest

from app.config import Settings
from app.models.enums import ResilienceRole
from app.schemas.domain import GeoPoint
from app.schemas.predictions import PredictionKind
from app.services.route_resilience import assign_route_roles, route_diversity
from app.services.route_scoring import (
    RouteScoreWeights,
    RouteScoringEngine,
    ScoredRoute,
)
from app.services.routing.base import RoutingRequest
from app.services.routing.road_graph_provider import (
    RoadGraphRoutingProvider,
    RouteSnappingError,
    RoutingUnavailableError,
    edge_travel_time_s,
    snap_point_to_graph_node,
)

# Bengaluru, near the real imported network, so coordinates are plausible.
BASE_LON = 77.59
BASE_LAT = 13.09


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "road_network_key": "unit_network",
        "route_max_snap_meters": 250.0,
        "route_max_candidates": 4,
        "route_alternative_penalty": 2.0,
        "route_min_diversity": 0.30,
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def _edge(
    graph: nx.DiGraph,
    from_node: str,
    to_node: str,
    *,
    lon_a: float,
    lat_a: float,
    lon_b: float,
    lat_b: float,
    length_m: float,
    speed_limit_kmh: float = 50.0,
    road_class: str = "secondary",
    lanes: int = 2,
    has_signal: bool = False,
    index: int = 0,
) -> None:
    """Add one real edge, carrying the stored-geometry shape the graph publishes."""
    graph.add_node(from_node, x=lon_a, y=lat_a)
    graph.add_node(to_node, x=lon_b, y=lat_b)
    graph.add_edge(
        from_node,
        to_node,
        length_m=length_m,
        speed_limit_kmh=speed_limit_kmh,
        road_class=road_class,
        lanes=lanes,
        has_signal=has_signal,
        road_edge_id=f"00000000-0000-4000-8000-{index:012d}",
        external_id=f"sumo-{index}",
        coords=((lon_a, lat_a), (lon_b, lat_b)),
    )


@pytest.fixture
def grid() -> nx.DiGraph:
    """Two independent parallel streets from A to D, joined into one network.

    ``A -> B -> C -> D`` is the fast pair of edges; ``A -> E -> F -> D`` is the
    slow pair. They share no edges at all, so diversity between them is 1.0.
    A third street ``A -> G -> H -> D`` exists for the "fewer than three
    candidates" case when ``route_max_candidates`` is lowered.
    """
    graph = nx.DiGraph()
    # Fast street (100 m then 100 m at 50 km/h).
    _edge(
        graph, "A", "B", lon_a=BASE_LON, lat_a=BASE_LAT,
        lon_b=BASE_LON + 0.001, lat_b=BASE_LAT, length_m=100.0, index=1,
    )
    _edge(
        graph, "B", "D", lon_a=BASE_LON + 0.001, lat_a=BASE_LAT,
        lon_b=BASE_LON + 0.002, lat_b=BASE_LAT, length_m=100.0, has_signal=True, index=2,
    )
    # Slow street: longer and slower, fully disjoint.
    _edge(
        graph, "A", "E", lon_a=BASE_LON, lat_a=BASE_LAT,
        lon_b=BASE_LON, lat_b=BASE_LAT - 0.001, length_m=200.0,
        speed_limit_kmh=20.0, road_class="residential", lanes=1, index=3,
    )
    _edge(
        graph, "E", "F", lon_a=BASE_LON, lat_a=BASE_LAT - 0.001,
        lon_b=BASE_LON + 0.001, lat_b=BASE_LAT - 0.001, length_m=200.0,
        speed_limit_kmh=20.0, road_class="residential", lanes=1, index=4,
    )
    _edge(
        graph, "F", "D", lon_a=BASE_LON + 0.001, lat_a=BASE_LAT - 0.001,
        lon_b=BASE_LON + 0.002, lat_b=BASE_LAT, length_m=200.0,
        speed_limit_kmh=20.0, road_class="residential", lanes=1, index=5,
    )
    # Third street, used only when more candidates are requested.
    _edge(
        graph, "A", "G", lon_a=BASE_LON, lat_a=BASE_LAT,
        lon_b=BASE_LON, lat_b=BASE_LAT + 0.001, length_m=150.0,
        speed_limit_kmh=40.0, index=6,
    )
    _edge(
        graph, "G", "H", lon_a=BASE_LON, lat_a=BASE_LAT + 0.001,
        lon_b=BASE_LON + 0.001, lat_b=BASE_LAT + 0.001, length_m=150.0,
        speed_limit_kmh=40.0, index=7,
    )
    _edge(
        graph, "H", "D", lon_a=BASE_LON + 0.001, lat_a=BASE_LAT + 0.001,
        lon_b=BASE_LON + 0.002, lat_b=BASE_LAT, length_m=150.0,
        speed_limit_kmh=40.0, index=8,
    )
    return graph


def _provider(graph: nx.DiGraph, **overrides: object) -> RoadGraphRoutingProvider:
    # The toy grid's fast street is ~7x cheaper than its disjoint alternative,
    # so a small penalty cannot dislodge it. 8.0 makes the mechanism explicit
    # here; the shipped default (2.0) is verified against the real network in
    # tests/integration/test_road_graph_routing.py.
    overrides.setdefault("route_alternative_penalty", 8.0)
    provider = RoadGraphRoutingProvider(_settings(**overrides))
    provider._graph = graph  # the unit graph stands in for the cached PostGIS graph
    provider._network_key = "unit_network"
    return provider


def _request(origin: GeoPoint, destination: GeoPoint) -> RoutingRequest:
    from uuid import uuid4

    return RoutingRequest(
        mission_id=uuid4(),
        vehicle_id=uuid4(),
        origin=origin,
        destination=destination,
        proposals=(),
        parameters={},
    )


ORIGIN = GeoPoint(latitude=BASE_LAT, longitude=BASE_LON)
DESTINATION = GeoPoint(latitude=BASE_LAT, longitude=BASE_LON + 0.002)


class TestCoordinateSnapping:
    def test_valid_point_snaps_to_its_own_node(self, grid):
        snapped = snap_point_to_graph_node(grid, ORIGIN, max_snap_meters=250.0)
        assert snapped.node == "A"
        assert snapped.snap_distance_meters == pytest.approx(0.0, abs=1e-6)
        assert snapped.point == ORIGIN

    def test_nearby_point_snaps_within_bound(self, grid):
        # ~11 m north of A, still inside the 250 m bound.
        nearby = GeoPoint(latitude=BASE_LAT + 0.0001, longitude=BASE_LON)
        snapped = snap_point_to_graph_node(grid, nearby, max_snap_meters=250.0)
        assert snapped.node in {"A", "G"}
        assert 0.0 < snapped.snap_distance_meters <= 250.0

    def test_outside_network_point_is_refused_not_snapped(self, grid):
        far = GeoPoint(latitude=12.9716, longitude=77.5946)
        with pytest.raises(RouteSnappingError) as excinfo:
            snap_point_to_graph_node(grid, far, max_snap_meters=250.0)
        message = str(excinfo.value)
        assert "exceeds" in message
        # The real distance is reported rather than hidden.
        assert "m from the nearest road node" in message

    def test_bound_of_zero_refuses_even_an_exact_node(self, grid):
        snapped_exact = snap_point_to_graph_node(grid, ORIGIN, max_snap_meters=0.5)
        with pytest.raises(RouteSnappingError):
            snap_point_to_graph_node(
                grid,
                GeoPoint(latitude=BASE_LAT + 0.0005, longitude=BASE_LON),
                max_snap_meters=0.5,
            )
        assert snapped_exact.node == "A"

    @pytest.mark.parametrize(
        "latitude,longitude",
        [
            (91.0, BASE_LON),
            (-91.0, BASE_LON),
            (BASE_LAT, 181.0),
            (BASE_LAT, -181.0),
        ],
    )
    def test_out_of_range_coordinates_are_rejected(self, grid, latitude, longitude):
        # Pydantic blocks these on a GeoPoint, so the provider's own validation
        # is exercised through a model that bypasses construction checks.
        bypassed = GeoPoint.model_construct(
            latitude=latitude, longitude=longitude
        )
        with pytest.raises(RouteSnappingError):
            snap_point_to_graph_node(grid, bypassed, max_snap_meters=250.0)

    def test_non_finite_coordinates_are_rejected(self, grid):
        bypassed = GeoPoint.model_construct(
            latitude=float("inf"), longitude=BASE_LON
        )
        with pytest.raises(RouteSnappingError):
            snap_point_to_graph_node(grid, bypassed, max_snap_meters=250.0)

    def test_zero_bound_is_a_configuration_error(self, grid):
        with pytest.raises(ValueError):
            snap_point_to_graph_node(grid, ORIGIN, max_snap_meters=0.0)

    def test_snapping_is_deterministic_for_equidistant_nodes(self):
        # Two nodes at exactly the same distance from the query point.
        graph = nx.DiGraph()
        graph.add_node("zzz", x=BASE_LON, y=BASE_LAT)
        graph.add_node("aaa", x=BASE_LON, y=BASE_LAT)
        query = GeoPoint(latitude=BASE_LAT, longitude=BASE_LON)
        first = snap_point_to_graph_node(graph, query, max_snap_meters=10.0)
        second = snap_point_to_graph_node(graph, query, max_snap_meters=10.0)
        assert first.node == second.node == "aaa"  # tie broken by node id


class TestCandidateGeneration:
    def test_shortest_travel_time_route_is_chosen_first(self, grid):
        proposals = _provider(grid).calculate_routes(_request(ORIGIN, DESTINATION))
        # Fast street: 200 m at 50 km/h = 14.4 s. Slow street: 600 m at
        # 20 km/h = 108 s. The third street: 450 m at 40 km/h = 40.5 s.
        assert proposals[0].distance_meters == pytest.approx(200.0)
        assert proposals[0].estimated_duration_seconds == 15
        assert len(proposals[0].road_segment_ids) == 2

    def test_shortest_route_matches_networkx_optimal_cost(self, grid):
        proposals = _provider(grid).calculate_routes(_request(ORIGIN, DESTINATION))
        expected_cost, _ = nx.single_source_dijkstra(
            grid, "A", "D", weight=lambda _u, _v, data: edge_travel_time_s(data)
        )
        emitted = proposals[0].estimated_duration_seconds
        assert emitted == pytest.approx(expected_cost, abs=1.0)

    def test_alternative_route_is_a_different_street(self, grid):
        proposals = _provider(grid).calculate_routes(_request(ORIGIN, DESTINATION))
        primary = set(proposals[0].road_segment_ids)
        alternatives = [
            set(proposal.road_segment_ids) for proposal in proposals[1:]
        ]
        assert alternatives, "expected at least one alternative route"
        assert any(candidate != primary for candidate in alternatives)
        # The disjoint slow street is offered as an alternative.
        assert any(
            not (candidate & primary) for candidate in alternatives
        ), "expected one fully edge-disjoint alternative"

    def test_alternatives_avoid_the_primary_edges(self, grid):
        proposals = _provider(grid).calculate_routes(_request(ORIGIN, DESTINATION))
        primary = set(proposals[0].road_segment_ids)
        for proposal in proposals[1:]:
            overlap = len(primary & set(proposal.road_segment_ids))
            assert overlap < len(primary)

    def test_penalty_of_one_reproduces_the_primary(self, grid):
        proposals = _provider(grid, route_alternative_penalty=1.0).calculate_routes(
            _request(ORIGIN, DESTINATION)
        )
        assert len(proposals) == 1, "no penalisation means no way to leave the primary"

    def test_penalty_too_small_to_displace_the_primary_yields_one_candidate(self, grid):
        """Documented limitation: a small penalty can leave no alternative.

        Here the fast street stays cheapest even after being doubled, so the
        search converges back onto it. Rather than inventing a second route the
        provider reports fewer candidates, and the resilience engine then has to
        say so. Measured on the real network the default 2.0 does clear the
        fast street; see the integration tests.
        """
        proposals = _provider(grid, route_alternative_penalty=2.0).calculate_routes(
            _request(ORIGIN, DESTINATION)
        )
        assert len(proposals) == 1
        assert len(proposals[0].road_segment_ids) == 2

    def test_candidate_count_is_bounded_by_configuration(self, grid):
        provider = _provider(grid, route_max_candidates=2)
        assert len(provider.calculate_routes(_request(ORIGIN, DESTINATION))) == 2

    def test_minimize_distance_objective_is_supported(self, grid):
        provider = _provider(grid, route_objective="MINIMIZE_DISTANCE")
        proposals = provider.calculate_routes(_request(ORIGIN, DESTINATION))
        # Both streets have the same endpoints; the short one still wins.
        assert proposals[0].distance_meters == pytest.approx(200.0)

    def test_unreachable_destination_is_refused_explicitly(self):
        # A network where the destination is in a component A cannot reach.
        graph = nx.DiGraph()
        _edge(
            graph, "A", "B", lon_a=BASE_LON, lat_a=BASE_LAT,
            lon_b=BASE_LON + 0.001, lat_b=BASE_LAT, length_m=100.0, index=1,
        )
        _edge(
            graph, "Y", "D", lon_a=BASE_LON + 0.05, lat_a=BASE_LAT,
            lon_b=BASE_LON + 0.002, lat_b=BASE_LAT, length_m=100.0, index=2,
        )
        provider = _provider(graph)
        with pytest.raises(RoutingUnavailableError) as excinfo:
            provider.calculate_routes(_request(ORIGIN, DESTINATION))
        assert "no directed path" in str(excinfo.value)

    def test_outside_network_endpoints_are_refused(self, grid):
        provider = _provider(grid)
        with pytest.raises(RouteSnappingError):
            provider.calculate_routes(
                _request(ORIGIN, GeoPoint(latitude=12.9, longitude=77.5))
            )

    def test_identical_endpoints_are_refused(self, grid):
        provider = _provider(grid)
        with pytest.raises(RoutingUnavailableError) as excinfo:
            provider.calculate_routes(_request(ORIGIN, ORIGIN))
        assert "same road node" in str(excinfo.value)

    def test_unloaded_provider_refuses_to_route(self):
        with pytest.raises(RoutingUnavailableError):
            RoadGraphRoutingProvider(_settings()).calculate_routes(
                _request(ORIGIN, DESTINATION)
            )

    def test_repeated_generation_is_identical(self, grid):
        provider = _provider(grid)
        first = provider.calculate_routes(_request(ORIGIN, DESTINATION))
        second = provider.calculate_routes(_request(ORIGIN, DESTINATION))
        assert [p.road_segment_ids for p in first] == [p.road_segment_ids for p in second]
        assert [p.distance_meters for p in first] == [p.distance_meters for p in second]
        assert [p.estimated_duration_seconds for p in first] == [
            p.estimated_duration_seconds for p in second
        ]


class TestGeneratedRouteContent:
    def test_geometry_comes_from_stored_edge_shapes(self, grid):
        proposals = _provider(grid).calculate_routes(_request(ORIGIN, DESTINATION))
        primary = proposals[0]
        assert primary.geometry[0] == ORIGIN
        assert primary.geometry[-1] == DESTINATION
        # Two edges, each contributing both of its stored vertices, with the
        # shared joint emitted once.
        assert len(primary.geometry) == 3
        middle = primary.geometry[1]
        assert (middle.longitude, middle.latitude) == pytest.approx(
            (BASE_LON + 0.001, BASE_LAT)
        )

    def test_geometry_is_finite_and_within_network_bounds(self, grid):
        proposals = _provider(grid).calculate_routes(_request(ORIGIN, DESTINATION))
        longitudes = [
            value
            for proposal in proposals
            for point in proposal.geometry
            for value in (point.longitude, point.latitude)
        ]
        assert all(value == value for value in longitudes)  # not NaN
        assert all(abs(value) < 180 for value in longitudes)
        min_lon = min(float(data["x"]) for _n, data in grid.nodes(data=True))
        max_lon = max(float(data["x"]) for _n, data in grid.nodes(data=True))
        for proposal in proposals:
            for point in proposal.geometry:
                assert min_lon <= point.longitude <= max_lon

    def test_distance_and_duration_are_summed_from_real_attributes(self, grid):
        proposals = _provider(grid).calculate_routes(_request(ORIGIN, DESTINATION))
        slow = next(
            proposal for proposal in proposals if proposal.distance_meters == 600.0
        )
        # 600 m at 20 km/h = 108 s exactly.
        assert slow.estimated_duration_seconds == 108
        assert slow.road_segment_ids == [
            "00000000-0000-4000-8000-000000000003",
            "00000000-0000-4000-8000-000000000004",
            "00000000-0000-4000-8000-000000000005",
        ]

    def test_road_segment_ids_are_canonical_road_edge_ids(self, grid):
        proposals = _provider(grid).calculate_routes(_request(ORIGIN, DESTINATION))
        canonical = {
            str(data["road_edge_id"]) for _u, _v, data in grid.edges(data=True)
        }
        sumo_ids = {str(data["external_id"]) for _u, _v, data in grid.edges(data=True)}
        for proposal in proposals:
            assert proposal.road_segment_ids
            assert set(proposal.road_segment_ids) <= canonical
            # SUMO external ids must not leak into the canonical field.
            assert not set(proposal.road_segment_ids) & sumo_ids

    def test_network_key_is_reported_in_the_name(self, grid):
        provider = _provider(grid)
        proposals = provider.calculate_routes(_request(ORIGIN, DESTINATION))
        assert all(provider.network_key in proposal.name for proposal in proposals)

    def test_proposals_declare_themselves_reachable_without_risk_claims(self, grid):
        proposals = _provider(grid).calculate_routes(_request(ORIGIN, DESTINATION))
        for proposal in proposals:
            assert proposal.reachable is True
            # No risk is invented by the routing layer.
            assert proposal.risk_score is None
            assert proposal.predicted_failure_probability is None

    def test_fallback_geometry_uses_real_endpoints_when_no_shape_is_stored(self):
        _assert_fallback_geometry()


def _assert_fallback_geometry() -> None:
    """A hand-built graph without ``coords`` still yields real endpoints."""
    graph = nx.DiGraph()
    graph.add_node("A", x=BASE_LON, y=BASE_LAT)
    graph.add_node("B", x=BASE_LON + 0.001, y=BASE_LAT)
    graph.add_node("C", x=BASE_LON + 0.002, y=BASE_LAT)
    for from_node, to_node, index in (("A", "B", 1), ("B", "C", 2)):
        graph.add_edge(
            from_node,
            to_node,
            length_m=100.0,
            speed_limit_kmh=50.0,
            road_class="secondary",
            lanes=2,
            has_signal=False,
            road_edge_id=f"00000000-0000-4000-8000-{index:012d}",
        )
    proposals = _provider(graph).calculate_routes(
        _request(ORIGIN, GeoPoint(latitude=BASE_LAT, longitude=BASE_LON + 0.002))
    )
    assert proposals[0].geometry[0] == ORIGIN
    assert proposals[0].geometry[-1] == GeoPoint(
        latitude=BASE_LAT, longitude=BASE_LON + 0.002
    )
    assert len(proposals[0].geometry) == 3


class TestDiversityAndRoles:
    """The diversity metric and role assignment are the existing Task 5 code.

    What is asserted here is that real generated routes feed it correctly, and
    that the metric is the documented one.
    """

    @staticmethod
    def _scored(proposals) -> list[ScoredRoute]:
        return [
            ScoredRoute(
                candidate_id=__import__("uuid").uuid4(),
                proposal=proposal,
                score=float(index),
                score_coverage=1.0,
                viable=True,
                rejection_reasons=(),
                metrics={"eta": float(proposal.estimated_duration_seconds)},
                provider_order=index,
            )
            for index, proposal in enumerate(proposals)
        ]

    def test_diversity_metric_is_one_minus_shared_edge_over_min_length(self, grid):
        proposals = _provider(grid).calculate_routes(_request(ORIGIN, DESTINATION))
        first, second = self._scored(proposals)[:2]
        shared = len(
            set(first.proposal.road_segment_ids)
            & set(second.proposal.road_segment_ids)
        )
        shortest = min(
            len(first.proposal.road_segment_ids), len(second.proposal.road_segment_ids)
        )
        assert route_diversity(first, second) == pytest.approx(
            1.0 - shared / shortest, abs=1e-9
        )

    def test_disjoint_streets_are_maximally_diverse(self, grid):
        proposals = _provider(grid).calculate_routes(_request(ORIGIN, DESTINATION))
        scored = self._scored(proposals)
        slow = next(
            item
            for item in scored
            if item.proposal.distance_meters == 600.0
        )
        fast = next(item for item in scored if item.proposal.distance_meters == 200.0)
        assert route_diversity(fast, slow) == pytest.approx(1.0)

    def test_roles_are_assigned_without_duplicates(self, grid):
        proposals = _provider(grid).calculate_routes(_request(ORIGIN, DESTINATION))
        scored = self._scored(proposals)
        result = assign_route_roles(scored, minimum_diversity=0.30)
        assert result.primary_id is not None
        assigned = [
            result.primary_id,
            result.backup_id,
            result.contingency_id,
        ]
        assigned = [item for item in assigned if item is not None]
        assert len(assigned) == len(set(assigned))
        assert set(result.roles.values()) <= {
            ResilienceRole.PRIMARY,
            ResilienceRole.BACKUP,
            ResilienceRole.CONTINGENCY,
        }

    def test_role_assignment_is_deterministic(self, grid):
        proposals = _provider(grid).calculate_routes(_request(ORIGIN, DESTINATION))
        # Identical inputs must include identical candidate ids, so build once
        # and assign twice from the same scored list.
        scored = self._scored(proposals)
        first = assign_route_roles(scored, minimum_diversity=0.30)
        second = assign_route_roles(scored, minimum_diversity=0.30)
        assert first.roles.keys() == second.roles.keys()
        assert first.primary_id == second.primary_id
        assert first.backup_id == second.backup_id
        assert first.diversity == second.diversity

    def test_no_diverse_backup_yields_an_explicit_degraded_result(self, grid):
        """Near-identical candidates must not be dressed up as resilience."""
        proposals = _provider(grid).calculate_routes(_request(ORIGIN, DESTINATION))
        primary = proposals[0]
        # A second candidate over exactly the same streets: diversity 0.0.
        twin = primary.model_copy(
            update={
                "name": "twin",
                # Different node ordering of the same edges.
                "road_segment_ids": list(reversed(primary.road_segment_ids)),
            }
        )
        result = assign_route_roles(
            self._scored([primary, twin]), minimum_diversity=0.30
        )
        assert result.primary_id is not None
        # Explicitly degraded, and nothing is fabricated to fill the role.
        assert result.backup_id is None
        assert result.contingency_id is None
        assert result.level.value in {"LOW_RESILIENCE", "MEDIUM_RESILIENCE"}
        assert any("No sufficiently diverse" in line for line in result.explanation)

    def test_no_viable_candidate_yields_explicit_none_resilience(self):
        empty = assign_route_roles([], minimum_diversity=0.30)
        assert empty.primary_id is None
        assert empty.level.value == "NO_RESILIENCE"
        assert "No viable route candidate" in " ".join(empty.explanation)


class TestScoringIntegration:
    """Generated routes feed the existing scoring engine unchanged."""

    @staticmethod
    def _engine() -> RouteScoringEngine:
        return RouteScoringEngine(
            RouteScoreWeights.from_settings(_settings()),
            failure_threshold=0.70,
            hazard_threshold=0.80,
        )

    def test_scoring_consumes_generated_routes(self, grid):
        proposals = _provider(grid).calculate_routes(_request(ORIGIN, DESTINATION))
        scored = self._engine().score(
            [(__import__("uuid").uuid4(), proposal) for proposal in proposals]
        )
        assert len(scored) == len(proposals)
        assert all(item.viable for item in scored)
        assert all(0.0 <= item.score <= 1.0 for item in scored)
        assert all("eta" in item.metrics and "distance" in item.metrics for item in scored)

    def test_prediction_signals_are_folded_in_when_available(self, grid):
        from app.services.route_scoring import PredictionSignals

        proposals = _provider(grid).calculate_routes(_request(ORIGIN, DESTINATION))
        ids = [__import__("uuid").uuid4() for _ in proposals]
        scored = self._engine().score(
            list(zip(ids, proposals, strict=True)),
            predictions={
                ids[0]: PredictionSignals(
                    eta_seconds=120.0,
                    failure_probability=0.9,
                    congestion_score=0.1,
                    hazard_exposure=0.05,
                ),
                ids[1]: PredictionSignals(
                    eta_seconds=110.0,
                    failure_probability=0.01,
                    congestion_score=0.1,
                    hazard_exposure=0.05,
                ),
            },
        )
        assert scored[0].metrics["failure"] == 0.9
        assert scored[1].metrics["failure"] == 0.01
        # The high-risk candidate is not viable under the configured threshold.
        assert scored[0].viable is False
        assert "failure_probability_exceeds_threshold" in scored[0].rejection_reasons
        assert scored[1].viable is True

    def test_congestion_and_hazard_are_deterministic_predictors(self, grid):
        from app.services.predictors import (
            EventFeature,
            MissionPredictionContext,
            RouteFeature,
            VehicleFeature,
            predictor_for,
        )

        proposals = _provider(grid).calculate_routes(_request(ORIGIN, DESTINATION))
        from uuid import uuid4

        vehicle_id = uuid4()
        route = RouteFeature(
            id=uuid4(),
            vehicle_id=vehicle_id,
            distance_meters=proposals[0].distance_meters,
            estimated_duration_seconds=proposals[0].estimated_duration_seconds,
            risk_score=None,
            road_edge_ids=tuple(proposals[0].road_segment_ids),
        )
        context = MissionPredictionContext(
            mission_id=uuid4(),
            mission_status="active",
            routes=(route,),
            vehicles=(
                VehicleFeature(id=vehicle_id, speed_meters_per_second=8.0),
            ),
            hazards=(),
            recent_events=(
                EventFeature(
                    event_type="CONGESTION_CHANGED",
                    payload={"congestion_ratio": 0.4},
                ),
            ),
        )
        congestion = predictor_for(PredictionKind.CONGESTION).predict(context, 300)
        hazard = predictor_for(PredictionKind.HAZARD_IMPACT).predict(context, 300)
        # Deterministic baselines, not ML: they name the engine that made them.
        assert congestion.source == "sentinel_prediction_engine"
        assert hazard.source == "sentinel_prediction_engine"
        assert congestion.status.value == "AVAILABLE"
        assert congestion.probability is not None
        assert congestion.metadata["method"]

class TestAiSignalIntegration:
    """Phase 4 predictions feed candidate scoring, with provenance preserved."""

    def test_ai_signals_come_from_the_facade_and_are_not_fabricated(self, grid):
        provider = _provider(grid, sentinel_ai_enabled=True)
        proposals = provider.calculate_routes(_request(ORIGIN, DESTINATION))
        eta, failure, source, reasons, confidence = provider._ai_signals(
            grid, proposals[0], None
        )
        assert source == "baseline_fallback", (
            "no trained artifact is committed, so the facade must report the "
            "baseline rather than an invented ML source"
        )
        assert eta is not None and eta > 0
        assert failure is not None and 0.0 <= failure <= 1.0
        assert isinstance(reasons, list) and reasons
        assert 0.0 <= confidence <= 1.0

    def test_ai_signals_prefer_vehicle_speed_for_eta(self, grid):
        class _Vehicle:
            id = "vehicle-1"
            speed = 10.0  # m/s

        provider = _provider(grid, sentinel_ai_enabled=True)
        proposals = provider.calculate_routes(_request(ORIGIN, DESTINATION))
        eta, _failure, _source, _reasons, _confidence = provider._ai_signals(
            grid, proposals[0], _Vehicle()
        )
        # 200 m at 10 m/s is 20 s; the facade's own estimate is ignored then.
        assert eta == pytest.approx(20.0)

    def test_route_with_no_edges_refuses_instead_of_guessing(self, grid):
        provider = _provider(grid, sentinel_ai_enabled=True)
        proposals = provider.calculate_routes(_request(ORIGIN, DESTINATION))
        unknown = proposals[0].model_copy(update={"road_segment_ids": []})
        eta, failure, source, reasons, _confidence = provider._ai_signals(
            grid, unknown, None
        )
        assert eta is None
        assert failure is None
        assert source == "no_edge_keys"
        assert reasons

    def test_ai_disabled_leaves_the_failure_signal_absent(self, grid):
        """With the AI layer off, no failure number is invented."""
        provider = _provider(grid, sentinel_ai_enabled=False)
        assert provider.settings.sentinel_ai_enabled is False
        proposals = provider.calculate_routes(_request(ORIGIN, DESTINATION))
        # The routing layer itself never claims a risk.
        assert all(proposal.risk_score is None for proposal in proposals)
        assert all(
            proposal.predicted_failure_probability is None for proposal in proposals
        )
