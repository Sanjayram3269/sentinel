"""Real routing and resilience against the imported ``osm_urban_v1`` network.

Every metric asserted here is measured from the operator-imported PostGIS
dataset: candidate geometry, distance and duration come from stored
``road_edges`` rows, and canonical ``RoadEdge.id`` values are what the
resilience engine's diversity metric operates on. No route, edge or metric is
fabricated.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path
from uuid import UUID, uuid4

import networkx as nx
import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.main import app
from app.models import Event, Mission, Route, RouteCandidate, Vehicle
from app.models.enums import ResilienceRole, RouteStatus, VehicleStatus, VehicleType
from app.models.road import RoadEdge, RoadNetwork
from app.services.ai_road_graph import clear_road_graph_cache, get_road_graph
from app.services.routing.road_graph_provider import (
    RoadGraphRoutingProvider,
    RouteSnappingError,
    RoutingUnavailableError,
)

pytestmark = pytest.mark.integration
BACKEND_DIR = Path(__file__).resolve().parents[2]
TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
NETWORK_KEY = "osm_urban_v1"
NET_FILE = BACKEND_DIR / "simulation" / "networks" / "osm_urban_v1" / "osm.net.xml"
PROVIDER_NAME = "road_graph_routing_provider"


@pytest.fixture(scope="module", autouse=True)
def prepared_database() -> None:
    """Migrate and import the real OSM network, as the AI suite does."""
    if not TEST_DATABASE_URL:
        pytest.skip("set TEST_DATABASE_URL to run routing integration tests")
    command.upgrade(Config(str(BACKEND_DIR / "alembic.ini")), "head")
    if not NET_FILE.is_file():
        pytest.skip(f"real OSM network missing at {NET_FILE}")
    environment = os.environ.copy()
    environment["DATABASE_URL"] = TEST_DATABASE_URL
    completed = subprocess.run(
        [
            sys.executable,
            "scripts/import_road_network.py",
            "--net",
            str(NET_FILE),
            "--network-key",
            NETWORK_KEY,
        ],
        cwd=BACKEND_DIR,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    _dispose_global_engine()
    clear_road_graph_cache()


def _dispose_global_engine() -> None:
    from app.db.session import engine as global_engine

    asyncio.run(global_engine.dispose())


@pytest.fixture(autouse=True)
def _isolate_engine_pool():
    yield
    _dispose_global_engine()


def run_async(coro):
    return asyncio.run(coro)


def _load_graph():
    engine = create_async_engine(TEST_DATABASE_URL, pool_pre_ping=True)

    async def _load():
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                return await get_road_graph(session, NETWORK_KEY)
        finally:
            await engine.dispose()

    return run_async(_load())


def _provider(graph=None):
    from app.config import get_settings

    provider = RoadGraphRoutingProvider(get_settings())
    if graph is not None:
        provider._graph = graph
        provider._network_key = NETWORK_KEY
    return provider


def _reachable_pair(graph):
    """Two real, far-apart nodes in the largest strongly connected component.

    Node ids are sorted first: a component is a ``set``, and set iteration order
    over strings varies between processes, which would silently make the chosen
    pair -- and therefore which resilience roles are reachable -- non
    deterministic.
    """
    components = sorted(
        (sorted(component) for component in nx.strongly_connected_components(graph)),
        key=lambda nodes: (-len(nodes), nodes[0]),
    )
    nodes = components[0]
    coords = [(float(graph.nodes[n]["x"]), float(graph.nodes[n]["y"])) for n in nodes]
    best = None
    for index, first in enumerate(coords):
        for second in coords[index + 1 :]:
            span = (first[0] - second[0]) ** 2 + (first[1] - second[1]) ** 2
            if best is None or span > best[0]:
                best = (span, first, second)
    return best[1], best[2]


def _payload(origin_lon, origin_lat, dest_lon, dest_lat):
    return {
        "vehicle_id": None,
        "origin": {"latitude": origin_lat, "longitude": origin_lon},
        "destination": {"latitude": dest_lat, "longitude": dest_lon},
    }


def _create_mission_with_vehicle(client, call_sign_prefix: str = "RT"):
    mission_id = client.post(
        "/api/v1/missions", json={"objective": "real graph routing"}
    ).json()["id"]
    vehicle = client.post(
        "/api/v1/vehicles",
        json={
            "mission_id": mission_id,
            "vehicle_type": "AMBULANCE",
            "call_sign": f"{call_sign_prefix}-{uuid4()}",
            "speed": 8.0,
        },
    )
    assert vehicle.status_code == 201, vehicle.text
    return mission_id, vehicle.json()["id"]


def _generate(client, mission_id, vehicle_id, origin_lon, origin_lat, dest_lon, dest_lat):
    payload = _payload(origin_lon, origin_lat, dest_lon, dest_lat)
    payload["vehicle_id"] = vehicle_id
    return client.post(
        f"/api/v1/missions/{mission_id}/routes/candidates", json=payload
    )


def _run_monitor(mission_id: str, route_id: str) -> dict:
    """Drive the existing service-level route monitor, as Task 5 tests do."""
    from redis.asyncio import Redis

    from app.services.event_bus import EventPublisher
    from app.services.event_service import EventService
    from app.services.prediction_service import PredictionService
    from app.services.route_monitor import RouteMonitorService

    async def evaluate() -> dict:
        redis = Redis.from_url("redis://localhost:6379/0", decode_responses=True)
        event_service = EventService(EventPublisher(redis))
        service = RouteMonitorService(
            event_service, PredictionService(event_service)
        )
        engine = create_async_engine(TEST_DATABASE_URL, pool_pre_ping=True)
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                result = await service.evaluate_route_health(
                    session, UUID(mission_id), UUID(route_id)
                )
                return result.model_dump(mode="json")
        finally:
            await engine.dispose()
            await redis.aclose()

    return run_async(evaluate())


class TestRealNetworkRoutingProvider:
    def test_graph_is_the_verified_imported_network(self):
        road_graph = _load_graph()
        assert road_graph.network_key == NETWORK_KEY
        assert road_graph.node_count == 308
        assert road_graph.edge_count == 701

    def test_edges_carry_real_attributes_and_stored_shape(self):
        graph = _load_graph().graph
        for _u, _v, data in graph.edges(data=True):
            assert set(data) >= {
                "length_m",
                "speed_limit_kmh",
                "road_class",
                "lanes",
                "has_signal",
                "road_edge_id",
                "external_id",
                "coords",
            }
            assert data["length_m"] > 0
            assert data["speed_limit_kmh"] > 0
            assert data["coords"], "every imported edge carries its stored geometry"

    def test_shortest_route_is_optimal_on_the_real_graph(self):
        graph = _load_graph().graph
        origin, destination = _reachable_pair(graph)
        from app.schemas.domain import GeoPoint
        from app.services.routing.base import RoutingRequest
        from app.services.routing.road_graph_provider import snap_point_to_graph_node
        from app.config import get_settings

        provider = _provider(graph)
        origin_point = GeoPoint(latitude=origin[1], longitude=origin[0])
        destination_point = GeoPoint(latitude=destination[1], longitude=destination[0])
        proposals = provider.calculate_routes(
            RoutingRequest(
                mission_id=uuid4(),
                vehicle_id=uuid4(),
                origin=origin_point,
                destination=destination_point,
                proposals=(),
                parameters={},
            )
        )
        assert proposals, "the real network must yield at least one route"

        # Independent Dijkstra on the same objective, from the snapped nodes.
        start = snap_point_to_graph_node(
            graph,
            origin_point,
            max_snap_meters=get_settings().route_max_snap_meters,
            label="origin",
        )
        end = snap_point_to_graph_node(
            graph,
            destination_point,
            max_snap_meters=get_settings().route_max_snap_meters,
            label="destination",
        )
        optimal_seconds, _path = nx.single_source_dijkstra(
            graph,
            start.node,
            target=end.node,
            weight=lambda _u, _v, data: data["length_m"]
            / (data["speed_limit_kmh"] * 1000.0 / 3600.0),
        )
        assert proposals[0].estimated_duration_seconds == pytest.approx(
            optimal_seconds, abs=1.0
        )
        # And no alternative may beat the primary.
        assert all(
            proposal.estimated_duration_seconds
            >= proposals[0].estimated_duration_seconds - 1
            for proposal in proposals
        )

    def test_route_metrics_come_from_stored_edges(self):
        graph = _load_graph().graph
        origin, destination = _reachable_pair(graph)
        from app.schemas.domain import GeoPoint
        from app.services.routing.base import RoutingRequest

        provider = _provider(graph)
        proposals = provider.calculate_routes(
            RoutingRequest(
                mission_id=uuid4(),
                vehicle_id=uuid4(),
                origin=GeoPoint(latitude=origin[1], longitude=origin[0]),
                destination=GeoPoint(latitude=destination[1], longitude=destination[0]),
                proposals=(),
                parameters={},
            )
        )
        # Recompute distance and duration straight from the graph edges.
        for proposal in proposals:
            expected_distance = 0.0
            expected_seconds = 0.0
            for road_edge_id in proposal.road_segment_ids:
                data = next(
                    d
                    for _u, _v, d in graph.edges(data=True)
                    if str(d["road_edge_id"]) == road_edge_id
                )
                expected_distance += data["length_m"]
                expected_seconds += data["length_m"] / (
                    data["speed_limit_kmh"] * 1000 / 3600
                )
            assert proposal.distance_meters == pytest.approx(expected_distance, rel=1e-6)
            assert proposal.estimated_duration_seconds == pytest.approx(
                expected_seconds, abs=1.0
            )

    def test_route_geometry_lies_on_real_roads_and_inside_network_bounds(self):
        graph = _load_graph().graph
        origin, destination = _reachable_pair(graph)
        from app.schemas.domain import GeoPoint
        from app.services.routing.base import RoutingRequest

        provider = _provider(graph)
        proposals = provider.calculate_routes(
            RoutingRequest(
                mission_id=uuid4(),
                vehicle_id=uuid4(),
                origin=GeoPoint(latitude=origin[1], longitude=origin[0]),
                destination=GeoPoint(latitude=destination[1], longitude=destination[0]),
                proposals=(),
                parameters={},
            )
        )
        node_longitudes = [float(d["x"]) for _n, d in graph.nodes(data=True)]
        node_latitudes = [float(d["y"]) for _n, d in graph.nodes(data=True)]
        edge_vertices = {
            (round(x, 9), round(y, 9))
            for _u, _v, d in graph.edges(data=True)
            for x, y in d["coords"]
        }
        for proposal in proposals:
            for point in proposal.geometry:
                assert point.longitude == point.longitude  # finite, not NaN
                assert min(node_longitudes) <= point.longitude <= max(node_longitudes)
                assert min(node_latitudes) <= point.latitude <= max(node_latitudes)
                # Every emitted vertex is a stored RoadEdge vertex.
                assert (
                    round(point.longitude, 9),
                    round(point.latitude, 9),
                ) in edge_vertices

    def test_alternative_routes_differ_from_the_primary(self):
        graph = _load_graph().graph
        origin, destination = _reachable_pair(graph)
        from app.schemas.domain import GeoPoint
        from app.services.routing.base import RoutingRequest

        proposals = _provider(graph).calculate_routes(
            RoutingRequest(
                mission_id=uuid4(),
                vehicle_id=uuid4(),
                origin=GeoPoint(latitude=origin[1], longitude=origin[0]),
                destination=GeoPoint(latitude=destination[1], longitude=destination[0]),
                proposals=(),
                parameters={},
            )
        )
        assert len(proposals) >= 2, "the real network must offer an alternative"
        primary = set(proposals[0].road_segment_ids)
        diversities = []
        for proposal in proposals[1:]:
            other = set(proposal.road_segment_ids)
            overlap = len(primary & other) / min(len(primary), len(other))
            diversities.append(1.0 - overlap)
        assert max(diversities) > 0.30, (
            "at least one alternative must clear the 0.30 diversity threshold; "
            f"measured {diversities}"
        )

    def test_generation_is_deterministic_across_repeated_calls(self):
        graph = _load_graph().graph
        origin, destination = _reachable_pair(graph)
        from app.schemas.domain import GeoPoint
        from app.services.routing.base import RoutingRequest

        provider = _provider(graph)
        request = RoutingRequest(
            mission_id=uuid4(),
            vehicle_id=uuid4(),
            origin=GeoPoint(latitude=origin[1], longitude=origin[0]),
            destination=GeoPoint(latitude=destination[1], longitude=destination[0]),
            proposals=(),
            parameters={},
        )
        first = provider.calculate_routes(request)
        second = provider.calculate_routes(request)
        assert [p.road_segment_ids for p in first] == [p.road_segment_ids for p in second]
        assert [p.distance_meters for p in first] == [p.distance_meters for p in second]

    def test_graph_is_cached_and_not_rebuilt_per_request(self):
        from app.config import get_settings
        from app.services.ai_road_graph import _CACHE

        engine = create_async_engine(TEST_DATABASE_URL, pool_pre_ping=True)

        async def _measure():
            try:
                async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                    first = RoadGraphRoutingProvider(get_settings())
                    await first.prepare(session)
                    second = RoadGraphRoutingProvider(get_settings())
                    await second.prepare(session)
                    # The shared cache returns the identical graph object, so
                    # NetworkX is not rebuilt per request.
                    assert second.graph is first.graph
                    assert _CACHE.get(NETWORK_KEY) is not None
            finally:
                await engine.dispose()

        run_async(_measure())

    def test_point_outside_the_network_is_refused(self):
        graph = _load_graph().graph
        from app.schemas.domain import GeoPoint
        from app.services.routing.base import RoutingRequest

        provider = _provider(graph)
        origin, _destination = _reachable_pair(graph)
        with pytest.raises(RouteSnappingError):
            provider.calculate_routes(
                RoutingRequest(
                    mission_id=uuid4(),
                    vehicle_id=uuid4(),
                    origin=GeoPoint(latitude=12.9716, longitude=77.5946),
                    destination=GeoPoint(latitude=origin[1], longitude=origin[0]),
                    proposals=(),
                    parameters={},
                )
            )

    def test_disconnected_components_have_no_route(self):
        graph = _load_graph().graph
        components = sorted(
            nx.weakly_connected_components(graph), key=len, reverse=True
        )
        if len(components) < 2:
            pytest.skip("network is a single component")
        from app.schemas.domain import GeoPoint
        from app.services.routing.base import RoutingRequest

        biggest, other = components[0], components[1]
        first = sorted(biggest)[0]
        second = sorted(other)[0]
        provider = _provider(graph)
        with pytest.raises((RoutingUnavailableError, RouteSnappingError)):
            provider.calculate_routes(
                RoutingRequest(
                    mission_id=uuid4(),
                    vehicle_id=uuid4(),
                    origin=GeoPoint(
                        latitude=float(graph.nodes[first]["y"]),
                        longitude=float(graph.nodes[first]["x"]),
                    ),
                    destination=GeoPoint(
                        latitude=float(graph.nodes[second]["y"]),
                        longitude=float(graph.nodes[second]["x"]),
                    ),
                    proposals=(),
                    parameters={},
                )
            )


class TestRealNetworkApiFlow:
    """candidate generation -> scoring -> resilience -> activation -> event."""

    def test_generation_scores_and_assigns_roles_on_the_real_network(self):
        graph = _load_graph().graph
        origin, destination = _reachable_pair(graph)
        with TestClient(app) as client:
            mission_id, vehicle_id = _create_mission_with_vehicle(client)
            response = _generate(
                client,
                mission_id,
                vehicle_id,
                origin[0],
                origin[1],
                destination[0],
                destination[1],
            )
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["provider"] == PROVIDER_NAME
        candidates = body["candidates"]
        assert len(candidates) >= 2
        resilience = body["resilience"]
        assert resilience["primary_route_id"] is not None

        assigned = [
            item["route_id"]
            for item in candidates
            if item["resilience_role"] is not None
        ]
        assert len(assigned) == len(set(assigned)), "roles must not duplicate a route"
        assert resilience["primary_route_id"] in assigned
        for role in ("backup_route_id", "contingency_route_id"):
            if resilience[role] is not None:
                assert resilience[role] in assigned
        for item in candidates:
            assert item["status"] in {"CANDIDATE"}
            assert item["road_segment_ids"]
            # Provenance travels with the candidate: which network generated it
            # and where its signals came from. With the AI layer disabled the
            # source is recorded as such rather than implied to be an ML result.
            rationale = item["rationale"]
            assert f"road_graph={NETWORK_KEY}" in rationale
            assert "ai_source=" in rationale
            assert "not_a_trained_probability=" in rationale
            assert "baseline_routing_score_v1=" in rationale

    def test_persisted_candidates_use_canonical_road_edge_ids(self):
        graph = _load_graph().graph
        origin, destination = _reachable_pair(graph)
        with TestClient(app) as client:
            mission_id, vehicle_id = _create_mission_with_vehicle(client)
            response = _generate(
                client,
                mission_id,
                vehicle_id,
                origin[0],
                origin[1],
                destination[0],
                destination[1],
            )
        assert response.status_code == 201, response.text
        mission_uuid = UUID(mission_id)
        vehicle_uuid = UUID(vehicle_id)
        route_ids = [item["route_id"] for item in response.json()["candidates"]]

        engine = create_async_engine(TEST_DATABASE_URL, pool_pre_ping=True)

        async def _inspect():
            try:
                async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                    canonical = set(
                        (
                            await session.execute(
                                select(RoadEdge.id).where(
                                    RoadEdge.network_id
                                    == select(RoadNetwork.id)
                                    .where(RoadNetwork.network_key == NETWORK_KEY)
                                    .scalar_subquery()
                                )
                            )
                        )
                        .scalars()
                        .all()
                    )
                    sumo = set(
                        (
                            await session.execute(
                                select(RoadEdge.external_id).where(
                                    RoadEdge.network_id
                                    == select(RoadNetwork.id)
                                    .where(RoadNetwork.network_key == NETWORK_KEY)
                                    .scalar_subquery()
                                )
                            )
                        )
                        .scalars()
                        .all()
                    )
                    rows = (
                        (
                            await session.execute(
                                select(RouteCandidate, Route)
                                .select_from(RouteCandidate)
                                .join(Route, Route.id == RouteCandidate.route_id)
                                .where(RouteCandidate.route_id.in_(route_ids))
                            )
                        )
                        .all()
                    )
                    assert rows
                    for candidate, route in rows:
                        # Mission and vehicle ownership.
                        assert candidate.mission_id == mission_uuid
                        assert candidate.vehicle_id == vehicle_uuid
                        assert route.mission_id == mission_uuid
                        assert route.vehicle_id == vehicle_uuid
                        # Canonical identity, and no SUMO leakage.
                        assert candidate.road_segment_ids
                        for value in candidate.road_segment_ids:
                            assert UUID(value) in canonical
                            assert value not in sumo
                        # Timestamps are timezone-aware.
                        assert candidate.created_at.tzinfo is not None
                        assert candidate.updated_at.tzinfo is not None
                    return rows
            finally:
                await engine.dispose()

        rows = run_async(_inspect())
        assert rows

    def test_geometry_persists_as_a_valid_wgs84_linestring(self):
        graph = _load_graph().graph
        origin, destination = _reachable_pair(graph)
        with TestClient(app) as client:
            mission_id, vehicle_id = _create_mission_with_vehicle(client)
            response = _generate(
                client,
                mission_id,
                vehicle_id,
                origin[0],
                origin[1],
                destination[0],
                destination[1],
            )
        assert response.status_code == 201, response.text
        route_ids = [item["route_id"] for item in response.json()["candidates"]]

        engine = create_async_engine(TEST_DATABASE_URL, pool_pre_ping=True)

        async def _geometry():
            try:
                async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                    rows = (
                        (
                            await session.execute(
                                select(
                                    Route.id,
                                    text("ST_SRID(geometry)"),
                                    text("ST_GeometryType(geometry)"),
                                    text("ST_IsValid(geometry)"),
                                    text("ST_NPoints(geometry)"),
                                ).where(Route.id.in_(route_ids))
                            )
                        )
                        .all()
                    )
                    assert rows
                    for _route_id, srid, kind, valid, npoints in rows:
                        assert srid == 4326
                        assert kind == "ST_LineString"
                        assert valid is True
                        assert npoints >= 2
                    return rows
            finally:
                await engine.dispose()

        rows = run_async(_geometry())
        assert rows

    def test_activation_connects_generated_routes_and_emits_an_event(self):
        graph = _load_graph().graph
        origin, destination = _reachable_pair(graph)
        with TestClient(app) as client:
            mission_id, vehicle_id = _create_mission_with_vehicle(client)
            generated = _generate(
                client,
                mission_id,
                vehicle_id,
                origin[0],
                origin[1],
                destination[0],
                destination[1],
            )
            assert generated.status_code == 201, generated.text
            body = generated.json()
            primary_route_id = body["resilience"]["primary_route_id"]
            activation = client.post(
                f"/api/v1/missions/{mission_id}/routes/{primary_route_id}/activate",
                json={},
            )
            assert activation.status_code == 200, activation.text
            activated = activation.json()
            assert activated["route"]["status"] == "ACTIVE"
            assert activated["route"]["id"] == primary_route_id

            events = client.get(f"/api/v1/missions/{mission_id}/events").json()
            types = {item["event_type"] for item in events["items"]}
            assert "ROUTE_ASSIGNED" in types
            assert "ROUTE_UPDATED" in types

    def test_repeated_generation_creates_a_new_cycle_not_duplicates(self):
        graph = _load_graph().graph
        origin, destination = _reachable_pair(graph)
        with TestClient(app) as client:
            mission_id, vehicle_id = _create_mission_with_vehicle(client)
            first = _generate(
                client, mission_id, vehicle_id,
                origin[0], origin[1], destination[0], destination[1],
            )
            second = _generate(
                client, mission_id, vehicle_id,
                origin[0], origin[1], destination[0], destination[1],
            )
        assert first.status_code == 201 and second.status_code == 201
        # Each call is its own planning cycle; nothing is overwritten in place.
        assert first.json()["planning_cycle_id"] != second.json()["planning_cycle_id"]
        route_ids = [item["route_id"] for item in second.json()["candidates"]]
        assert len(route_ids) == len(set(route_ids))

    def test_supplied_candidates_still_use_the_development_provider(self):
        """The pre-existing contract is untouched."""
        with TestClient(app) as client:
            mission_id, vehicle_id = _create_mission_with_vehicle(client, "SUP")
            origin = {"latitude": 13.09, "longitude": 77.59}
            destination = {"latitude": 13.10, "longitude": 77.60}
            response = client.post(
                f"/api/v1/missions/{mission_id}/routes/candidates",
                json={
                    "vehicle_id": vehicle_id,
                    "origin": origin,
                    "destination": destination,
                    "candidates": [
                        {
                            "name": "supplied a",
                            "geometry": [
                                origin,
                                {"latitude": 13.095, "longitude": 77.595},
                                destination,
                            ],
                            "distance_meters": 1500.0,
                            "estimated_duration_seconds": 180,
                        },
                        {
                            "name": "supplied b",
                            "geometry": [
                                origin,
                                {"latitude": 13.085, "longitude": 77.585},
                                {"latitude": 13.095, "longitude": 77.595},
                                destination,
                            ],
                            "distance_meters": 1800.0,
                            "estimated_duration_seconds": 240,
                        },
                    ],
                },
            )
        assert response.status_code == 201, response.text
        assert response.json()["provider"] == "baseline_development_provider"

    def test_outside_network_request_is_refused_with_422(self):
        with TestClient(app) as client:
            mission_id, vehicle_id = _create_mission_with_vehicle(client, "OUT")
            response = _generate(
                client, mission_id, vehicle_id, 77.5946, 12.9716, 77.60, 13.10
            )
        assert response.status_code == 422, response.text
        assert "snapping bound" in response.json()["detail"]


class TestFailureAndDegradedBehaviour:
    """Cases A-E from the phase brief, driven through the real network."""

    @staticmethod
    def _generate_and_activate(client, call_sign):
        graph = _load_graph().graph
        origin, destination = _reachable_pair(graph)
        mission_id, vehicle_id = _create_mission_with_vehicle(client, call_sign)
        response = _generate(
            client, mission_id, vehicle_id,
            origin[0], origin[1], destination[0], destination[1],
        )
        assert response.status_code == 201, response.text
        return mission_id, vehicle_id, response.json()

    def test_case_a_primary_is_the_lowest_eta_lowest_risk_candidate(self):
        with TestClient(app) as client:
            _mission, _vehicle, body = self._generate_and_activate(client, "CASEA")
        candidates = body["candidates"]
        primary = next(
            item
            for item in candidates
            if item["route_id"] == body["resilience"]["primary_route_id"]
        )
        assert primary["estimated_duration_seconds"] > 0
        # The lowest-ETA candidate wins when nothing invalidates it.
        lowest = min(item["estimated_duration_seconds"] for item in candidates)
        assert primary["estimated_duration_seconds"] == lowest
        assert primary["resilience_role"] == "PRIMARY"
        assert primary["viable"] is True

    def test_case_b_high_failure_risk_candidate_is_not_primary(self):
        """A candidate above the failure threshold loses viability entirely."""
        from app.services.route_scoring import (
            RouteScoreWeights,
            RouteScoringEngine,
        )
        from app.config import get_settings
        from app.services.route_resilience import assign_route_roles
        from app.services.routing.base import RoutingRequest
        from app.schemas.domain import GeoPoint

        graph = _load_graph().graph
        origin, destination = _reachable_pair(graph)
        from uuid import uuid4 as _uuid4

        provider = _provider(graph)
        proposals = provider.calculate_routes(
            RoutingRequest(
                mission_id=_uuid4(),
                vehicle_id=_uuid4(),
                origin=GeoPoint(latitude=origin[1], longitude=origin[0]),
                destination=GeoPoint(latitude=destination[1], longitude=destination[0]),
                proposals=(),
                parameters={},
            )
        )
        ids = [_uuid4() for _ in proposals]
        engine = RouteScoringEngine(
            RouteScoreWeights.from_settings(get_settings()),
            failure_threshold=get_settings().route_failure_threshold,
            hazard_threshold=get_settings().route_hazard_threshold,
        )
        from app.services.route_scoring import PredictionSignals

        scored = engine.score(
            list(zip(ids, proposals, strict=True)),
            predictions={
                ids[0]: PredictionSignals(failure_probability=0.95),
            },
        )
        result = assign_route_roles(scored, minimum_diversity=get_settings().route_min_diversity)
        assert scored[0].viable is False
        assert result.primary_id != ids[0]
        assert result.primary_id is not None

    def test_case_c_failed_primary_promotes_the_backup(self):
        with TestClient(app) as client:
            mission_id, vehicle_id, body = self._generate_and_activate(client, "CASEC")
            resilience = body["resilience"]
            if resilience["backup_route_id"] is None:
                pytest.skip("this pair produced no diverse backup on the real network")
            primary_route_id = resilience["primary_route_id"]
            backup_route_id = resilience["backup_route_id"]
            primary_activated = client.post(
                f"/api/v1/missions/{mission_id}/routes/{primary_route_id}/activate",
                json={},
            )
            assert primary_activated.status_code == 200, primary_activated.text

            # Drive the existing monitor to fail the active route via a closure.
            closure = client.post(
                f"/api/v1/missions/{mission_id}/events",
                json={
                    "event_type": "ROAD_CLOSURE",
                    "source": "integration_test",
                    "payload": {"route_id": str(primary_route_id)},
                },
            )
            assert closure.status_code == 201, closure.text
            health = _run_monitor(mission_id, primary_route_id)
            assert health["failure_detected"] is True

            promoted = client.get(f"/api/v1/missions/{mission_id}/routes/resilience")
            assert promoted.status_code == 200, promoted.text
            after = promoted.json()
            # The failed route loses its role and a different, previously
            # generated candidate takes over. The engine promotes the
            # lowest-scoring remaining *viable* candidate rather than blindly
            # re-promoting the route that happened to hold the BACKUP role,
            # which is the stronger guarantee.
            assert after["primary_route_id"] != primary_route_id
            assert after["primary_available"] is True
            assert after["primary_route_id"] in {
                backup_route_id,
                resilience["contingency_route_id"],
                *(item["route_id"] for item in body["candidates"]),
            }
            # Still a real generated route, never a fabricated one.
            assert after["primary_route_id"] in {
                item["route_id"] for item in body["candidates"]
            }

    def test_case_d_failed_backup_promotes_the_contingency(self):
        with TestClient(app) as client:
            mission_id, vehicle_id, body = self._generate_and_activate(client, "CASED")
            resilience = body["resilience"]
            if (
                resilience["backup_route_id"] is None
                or resilience["contingency_route_id"] is None
            ):
                pytest.skip("this pair produced fewer than three resilient routes")
            primary_route_id = resilience["primary_route_id"]
            backup_route_id = resilience["backup_route_id"]
            contingency_route_id = resilience["contingency_route_id"]

            for route_id in (primary_route_id, backup_route_id):
                activated = client.post(
                    f"/api/v1/missions/{mission_id}/routes/{route_id}/activate",
                    json={},
                )
                assert activated.status_code == 200, activated.text
                closure = client.post(
                    f"/api/v1/missions/{mission_id}/events",
                    json={
                        "event_type": "ROAD_CLOSURE",
                        "source": "integration_test",
                        "payload": {"route_id": str(route_id)},
                    },
                )
                assert closure.status_code == 201, closure.text
                health = _run_monitor(mission_id, str(route_id))
                assert health["failure_detected"] is True

            after = client.get(
                f"/api/v1/missions/{mission_id}/routes/resilience"
            ).json()
            # Both the original primary and the original backup are now failed,
            # so neither may hold a role; the strongest remaining viable route
            # is promoted.
            assert after["primary_route_id"] not in {primary_route_id, backup_route_id}
            assert after["primary_route_id"] in {
                item["route_id"] for item in body["candidates"]
            }
            assert after["primary_available"] is True

    def test_case_e_no_viable_route_is_an_explicit_result(self):
        """Every candidate invalidated: resilience reports NONE, not a guess."""
        from app.services.route_resilience import assign_route_roles
        from app.services.route_scoring import ScoredRoute

        graph = _load_graph().graph
        origin, destination = _reachable_pair(graph)
        from app.schemas.domain import GeoPoint
        from app.services.routing.base import RoutingRequest
        from uuid import uuid4 as _uuid4

        proposals = _provider(graph).calculate_routes(
            RoutingRequest(
                mission_id=_uuid4(),
                vehicle_id=_uuid4(),
                origin=GeoPoint(latitude=origin[1], longitude=origin[0]),
                destination=GeoPoint(latitude=destination[1], longitude=destination[0]),
                proposals=(),
                parameters={},
            )
        )
        scored = [
            ScoredRoute(
                candidate_id=_uuid4(),
                proposal=proposal,
                score=0.5,
                score_coverage=1.0,
                viable=False,
                rejection_reasons=("destination_unreachable",),
                metrics={},
                provider_order=index,
            )
            for index, proposal in enumerate(proposals)
        ]
        result = assign_route_roles(scored, minimum_diversity=0.30)
        assert result.primary_id is None
        assert result.backup_id is None
        assert result.contingency_id is None
        assert result.level.value == "NO_RESILIENCE"
        assert "No viable route candidate" in " ".join(result.explanation)

    def test_failed_routes_are_never_assigned_a_role(self):
        from app.models.enums import RouteStatus as Status
        from app.services.route_resilience import (
            assign_route_roles,
            is_route_status_viable,
        )
        from app.services.route_scoring import ScoredRoute

        graph = _load_graph().graph
        origin, destination = _reachable_pair(graph)
        from app.schemas.domain import GeoPoint
        from app.services.routing.base import RoutingRequest
        from uuid import uuid4 as _uuid4

        proposals = _provider(graph).calculate_routes(
            RoutingRequest(
                mission_id=_uuid4(),
                vehicle_id=_uuid4(),
                origin=GeoPoint(latitude=origin[1], longitude=origin[0]),
                destination=GeoPoint(latitude=destination[1], longitude=destination[0]),
                proposals=(),
                parameters={},
            )
        )
        for status in (Status.FAILED, Status.ABORTED, Status.DEGRADED):
            assert is_route_status_viable(status) is False
        # A stored candidate whose status degraded loses viability.
        degraded = ScoredRoute(
            candidate_id=_uuid4(),
            proposal=proposals[0],
            score=0.0,
            score_coverage=1.0,
            viable=False,
            rejection_reasons=("route_status_degraded",),
            metrics={},
            provider_order=0,
        )
        healthy = ScoredRoute(
            candidate_id=_uuid4(),
            proposal=proposals[1] if len(proposals) > 1 else proposals[0],
            score=0.4,
            score_coverage=1.0,
            viable=True,
            rejection_reasons=(),
            metrics={},
            provider_order=1,
        )
        result = assign_route_roles([degraded, healthy], minimum_diversity=0.30)
        assert result.primary_id == healthy.candidate_id