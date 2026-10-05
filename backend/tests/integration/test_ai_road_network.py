"""AI integration against the real imported PostGIS road network.

Every assertion here runs against the network produced by the operator-run OSM
importer. Nothing is faked: node coordinates, edge attributes and geometry all
come from ``road_edges``.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from geoalchemy2 import WKTElement
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.main import app
from app.models import Mission, Route, Vehicle
from app.models.enums import RouteStatus, VehicleStatus, VehicleType
from app.services.ai_road_graph import clear_road_graph_cache, get_road_graph
from app.services.predictors import RouteFeature
from app.schemas.predictions import PredictionKind

pytestmark = pytest.mark.integration
BACKEND_DIR = Path(__file__).resolve().parents[2]
REPO_DIR = BACKEND_DIR.parent
TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
NETWORK_KEY = "osm_urban_v1"
NET_FILE = BACKEND_DIR / "simulation" / "networks" / "osm_urban_v1" / "osm.net.xml"

from sentinel_ai.contracts import PredictEtaRequest, WorldState
from sentinel_ai.data.loader import load_dataset, summarise
from sentinel_ai.prediction.features import compute_route_features
from sentinel_ai.prediction.inference import FEATURE_COLS, reset_model_cache
from app.services.ai_predictor import AiPredictor


@pytest.fixture(scope="module", autouse=True)
def prepared_database() -> None:
    """Migrate the test database and import the real OSM network into it.

    The import runs as a subprocess against ``TEST_DATABASE_URL``. Doing it
    in-process would require rebinding ``app.db.session``'s engine, and
    reloading that module swaps the engine object out from under the
    application's dependency-injected sessions, leaving the shared pool
    holding connections bound to a closed event loop.
    """
    if not TEST_DATABASE_URL:
        pytest.skip("set TEST_DATABASE_URL to run AI road-network integration tests")
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
    assert "edges_imported" in completed.stdout, completed.stdout

    _dispose_global_engine()
    clear_road_graph_cache()
    reset_model_cache()


def _dispose_global_engine() -> None:
    """Empty the shared engine pool so no dead-loop connection is reused."""
    from app.db.session import engine as global_engine

    asyncio.run(global_engine.dispose())


@pytest.fixture(autouse=True)
def _isolate_engine_pool():
    """Keep the module-global engine's pool from crossing event loops.

    Each ``TestClient`` block serves on its own portal loop and closes it on
    exit, while ``run_async`` uses yet another. A pooled connection created on
    a loop that has since closed fails on reuse, so the pool is emptied after
    every test.
    """
    yield
    _dispose_global_engine()


@pytest.fixture
def engine():
    return create_async_engine(TEST_DATABASE_URL, pool_pre_ping=True)


def run_async(coro):
    """Run one coroutine on a fresh loop and return its result."""
    return asyncio.run(coro)


async def _road_graph(db, network_key: str = NETWORK_KEY):
    return await get_road_graph(db, network_key)


def _fetch_graph():
    engine = create_async_engine(TEST_DATABASE_URL, pool_pre_ping=True)

    async def _load():
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                return await _road_graph(session)
        finally:
            await engine.dispose()

    return run_async(_load())


def _real_route_edge_ids(graph, count: int = 6) -> list[str]:
    """Return a real directed path's edge ids from the imported network."""
    import networkx as nx

    # A deterministic walk over real edges. Start at the smallest node that
    # actually has an outgoing edge: the smallest node overall can be a sink.
    starts = [node for node in sorted(graph.nodes()) if graph.out_degree(node) > 0]
    assert starts, "imported graph has no traversable edges"
    edges: list[str] = []
    current = starts[0]
    for _ in range(count):
        successors = sorted(graph.successors(current))
        if not successors:
            break
        nxt = successors[0]
        edges.append(f"{current}->{nxt}")
        current = nxt
    assert edges, "imported graph has no traversable edges"
    return edges


def _route_linestring(graph, edge_ids: list[str]) -> str:
    """Build a LINESTRING that follows the real path, join points deduplicated."""
    coords: list[tuple[float, float]] = []
    for edge_id in edge_ids:
        u, v = edge_id.split("->")
        xs = [float(graph.nodes[u]["x"]), float(graph.nodes[v]["x"])]
        ys = [float(graph.nodes[u]["y"]), float(graph.nodes[v]["y"])]
        if not coords:
            coords.append((xs[0], ys[0]))
        coords.append((xs[1], ys[1]))
    parts = ",".join(f"{x} {y}" for x, y in coords)
    return f"LINESTRING({parts})"


def _seed_mission_and_route(mission_id: str, edge_ids: list[str], wkt: str) -> str:
    """Persist a mission, vehicle and an ACTIVE route on the real network."""
    vehicle_id = str(uuid4())

    async def persist() -> None:
        engine = create_async_engine(TEST_DATABASE_URL, pool_pre_ping=True)
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                async with session.begin():
                    mission = await session.get(Mission, UUID(mission_id))
                    assert mission is not None
                    vehicle = Vehicle(
                        id=UUID(vehicle_id),
                        mission_id=mission.id,
                        vehicle_type=VehicleType.AMBULANCE,
                        status=VehicleStatus.EN_ROUTE,
                        call_sign=f"AI-{uuid4()}",
                        speed=8.0,
                    )
                    route = Route(
                        mission_id=mission.id,
                        vehicle_id=UUID(vehicle_id),
                        status=RouteStatus.ACTIVE,
                        name="AI real-network route",
                        geometry=WKTElement(wkt, srid=4326),
                        distance_meters=1200.0,
                        estimated_duration_seconds=180,
                        risk_score=0.3,
                    )
                    session.add_all([vehicle, route])
        finally:
            await engine.dispose()

    run_async(persist())
    return vehicle_id


def _build_context(mission_id: str):
    """Build the prediction context for a mission on a single event loop."""
    from app.services.prediction_context import build_prediction_context

    engine = create_async_engine(TEST_DATABASE_URL, pool_pre_ping=True)

    async def _load():
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                return await build_prediction_context(session, UUID(mission_id))
        finally:
            await engine.dispose()

    return run_async(_load())


class TestRealRoadGraph:
    def test_graph_is_built_from_real_imported_edges(self):
        road_graph = _fetch_graph()
        # 712 stored edges collapse to 701 in a DiGraph (parallel pairs).
        assert road_graph.edge_count == 701
        assert road_graph.node_count == 308
        assert road_graph.network_key == NETWORK_KEY

    def test_graph_is_cached_not_rebuilt_per_prediction(self):
        engine = create_async_engine(TEST_DATABASE_URL, pool_pre_ping=True)

        async def _twice():
            async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                first = await _road_graph(session)
                second = await _road_graph(session)
                return first.graph is second.graph

        try:
            assert run_async(_twice()) is True
        finally:
            run_async(engine.dispose())

    def test_real_node_coordinates_are_wgs84(self):
        road_graph = _fetch_graph()
        node = sorted(road_graph.graph.nodes())[0]
        x, y = road_graph.graph.nodes[node]["x"], road_graph.graph.nodes[node]["y"]
        # Bengaluru bounding box, not a synthetic 200 m grid.
        assert 77.5 < x < 77.7
        assert 13.0 < y < 13.2

    def test_real_edges_carry_the_five_required_attributes(self):
        road_graph = _fetch_graph()
        required = {"length_m", "speed_limit_kmh", "road_class", "lanes", "has_signal"}
        checked = 0
        for _u, _v, data in road_graph.graph.edges(data=True):
            assert required <= set(data), f"missing {required - set(data)}"
            assert data["length_m"] > 0
            assert data["speed_limit_kmh"] > 0
            assert data["lanes"] >= 1
            assert isinstance(data["has_signal"], bool)
            checked += 1
        assert checked == 701

    def test_real_edge_geometry_is_stored_as_linestring_4326(self):
        engine = create_async_engine(TEST_DATABASE_URL, pool_pre_ping=True)

        async def _check():
            async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                row = (
                    await session.execute(
                        text(
                            "SELECT count(*), min(ST_SRID(geometry)), "
                            "count(*) FILTER (WHERE NOT ST_IsValid(geometry)) "
                            "FROM road_edges e "
                            "JOIN road_networks n ON n.id = e.network_id "
                            "WHERE n.network_key = :network_key"
                        ),
                        {"network_key": NETWORK_KEY},
                    )
                ).one()
                return row

        try:
            rows, srid, invalid = run_async(_check())
            assert rows == 712
            assert srid == 4326
            assert invalid == 0
        finally:
            run_async(engine.dispose())


class TestRealFeatureExtraction:
    def test_real_route_produces_the_trained_feature_vector(self):
        road_graph = _fetch_graph()
        edge_ids = _real_route_edge_ids(road_graph.graph)
        world_state = WorldState(
            timestamp_utc=1_672_531_200.0,
            demand_level=1.0,
            closed_edges=[],
            incidents=[],
            hazards=[],
            units=[],
            hospitals=[],
        )
        features = compute_route_features(road_graph.graph, edge_ids, 1_672_531_200.0, world_state)
        missing = [column for column in FEATURE_COLS if column not in features]
        assert not missing, missing
        assert features["length"] > 0
        assert features["free_flow_time"] > 0
        assert features["closed_edge_flag"] == 0.0

    def test_real_route_features_use_real_edge_attributes(self):
        road_graph = _fetch_graph()
        edge_ids = _real_route_edge_ids(road_graph.graph, count=4)
        world_state = WorldState(
            timestamp_utc=1_672_531_200.0,
            demand_level=1.0,
            closed_edges=[],
            incidents=[],
            hazards=[],
            units=[],
            hospitals=[],
        )
        features = compute_route_features(road_graph.graph, edge_ids, 1_672_531_200.0, world_state)
        # Free-flow minutes recomputed by hand from stored metres and km/h.
        seconds = 0.0
        for edge_id in edge_ids:
            u, v = edge_id.split("->")
            data = road_graph.graph[u][v]
            seconds += data["length_m"] / (data["speed_limit_kmh"] * 1000.0 / 3600.0)
        assert features["free_flow_time"] == pytest.approx(seconds / 60.0, rel=1e-9)


class TestAdaptersOnRealNetwork:
    def _mission_with_real_route(self, client: TestClient):
        road_graph = _fetch_graph()
        edge_ids = _real_route_edge_ids(road_graph.graph)
        wkt = _route_linestring(road_graph.graph, edge_ids)
        mission_id = client.post(
            "/api/v1/missions", json={"objective": "AI real-network prediction"}
        ).json()["id"]
        _seed_mission_and_route(mission_id, edge_ids, wkt)
        return mission_id, edge_ids

    def test_context_matches_route_to_real_road_edges(self):
        road_graph = _fetch_graph()
        edge_ids = _real_route_edge_ids(road_graph.graph)
        wkt = _route_linestring(road_graph.graph, edge_ids)
        with TestClient(app) as client:
            mission_id = client.post(
                "/api/v1/missions", json={"objective": "context match"}
            ).json()["id"]
            _seed_mission_and_route(mission_id, edge_ids, wkt)
            context = _build_context(mission_id)
        assert context.routes, "no active routes in context"
        matched = context.routes[0].road_edge_ids
        assert matched, "route geometry did not match any imported road edge"
        # Every matched edge must exist in the real graph.
        for edge_id in matched:
            u, v = edge_id.split("->")
            assert road_graph.graph.has_edge(u, v), edge_id

    def test_eta_adapter_reports_baseline_fallback_without_artifact(self):
        road_graph = _fetch_graph()
        edge_ids = _real_route_edge_ids(road_graph.graph)
        wkt = _route_linestring(road_graph.graph, edge_ids)
        with TestClient(app) as client:
            mission_id = client.post(
                "/api/v1/missions", json={"objective": "AI eta"}
            ).json()["id"]
            _seed_mission_and_route(mission_id, edge_ids, wkt)
            context = _build_context(mission_id)

        engine = create_async_engine(TEST_DATABASE_URL, pool_pre_ping=True)

        async def _predict():
            try:
                async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                    adapter = AiPredictor(session, NETWORK_KEY, PredictionKind.ETA)
                    return await adapter.predict(context, 300)
            finally:
                await engine.dispose()

        result = run_async(_predict())
        assert result.prediction_type is PredictionKind.ETA
        assert result.source == "sentinel_ai_road_graph"
        assert result.metadata["ai_source"] == "baseline_fallback"
        assert result.metadata["network_key"] == NETWORK_KEY
        assert result.metadata["graph_edges"] == 701
        assert result.metadata["eta_unit"] == "minutes"
        # Backward-compatible ETA value shape.
        assert "routes" in result.value
        entry = result.value["routes"][0]
        assert {"route_id", "vehicle_id", "eta_seconds", "method"} <= set(entry)
        assert entry["eta_seconds"] > 0
        assert result.factors

    def test_route_failure_adapter_reports_baseline_fallback(self):
        road_graph = _fetch_graph()
        edge_ids = _real_route_edge_ids(road_graph.graph)
        wkt = _route_linestring(road_graph.graph, edge_ids)
        with TestClient(app) as client:
            mission_id = client.post(
                "/api/v1/missions", json={"objective": "AI route failure"}
            ).json()["id"]
            _seed_mission_and_route(mission_id, edge_ids, wkt)
            context = _build_context(mission_id)

        engine = create_async_engine(TEST_DATABASE_URL, pool_pre_ping=True)

        async def _predict():
            try:
                async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                    adapter = AiPredictor(session, NETWORK_KEY, PredictionKind.ROUTE_FAILURE)
                    return await adapter.predict(context, 300)
            finally:
                await engine.dispose()

        result = run_async(_predict())
        assert result.prediction_type is PredictionKind.ROUTE_FAILURE
        assert result.source == "sentinel_ai_road_graph"
        assert result.metadata["ai_source"] == "baseline_fallback"
        assert result.metadata["not_a_trained_probability"] is True
        assert 0.0 <= result.probability <= 1.0

    def test_adapter_falls_back_when_network_is_missing(self):
        """An absent network must degrade, not fabricate."""
        road_graph = _fetch_graph()
        edge_ids = _real_route_edge_ids(road_graph.graph, count=2)
        context_route = RouteFeature(
            id=uuid4(),
            vehicle_id=uuid4(),
            distance_meters=100.0,
            estimated_duration_seconds=60,
            risk_score=None,
            road_edge_ids=tuple(edge_ids),
        )
        from app.services.predictors import MissionPredictionContext

        context = MissionPredictionContext(
            mission_id=uuid4(),
            mission_status="active",
            routes=(context_route,),
            vehicles=(),
            hazards=(),
            recent_events=(),
        )
        engine = create_async_engine(TEST_DATABASE_URL, pool_pre_ping=True)

        async def _predict():
            try:
                async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                    adapter = AiPredictor(session, "no_such_network_key", PredictionKind.ETA)
                    return await adapter.predict(context, 300)
            finally:
                await engine.dispose()

        result = run_async(_predict())
        # Falls back to the deterministic predictor's source, not the AI source.
        assert result.source == "sentinel_prediction_engine"


class TestRealNetworkDataset:
    """The previously NotImplementedError SUMO loader, on real road data."""

    def test_loader_builds_rows_from_the_imported_network(self):
        road_graph = _fetch_graph()
        rows = load_dataset(
            "sumo",
            graph=road_graph.graph,
            num_scenarios=3,
            od_pairs_per_scenario=5,
            paths_per_pair=2,
        )
        assert rows, "loader produced nothing from the imported network"
        summary = summarise(rows)
        assert summary["rows"] == len(rows)
        assert summary["accuracy_evaluated"] is False
        assert summary["label_source"] == "deterministic_simulator"
        for row in rows:
            missing = [column for column in FEATURE_COLS if column not in row]
            assert not missing, missing
            # ETA is minutes, distance is metres.
            assert row["actual_eta_min"] > 0
            assert row["length"] > 0
            assert 0.0 <= row["share_arterial"] <= 1.0

    def test_loader_features_are_measured_in_metres_not_degrees(self):
        road_graph = _fetch_graph()
        rows = load_dataset(
            "sumo",
            graph=road_graph.graph,
            num_scenarios=3,
            od_pairs_per_scenario=5,
            paths_per_pair=2,
        )
        distances = [row["min_distance_incident"] for row in rows]
        # The city spans roughly 2.7 km. Before distances were converted from
        # WGS84 degrees these values were all around 0.0005.
        assert min(distances) > 1.0, distances[:5]
        assert max(distances) < 100_000.0, max(distances)

    def test_real_graph_declares_its_coordinate_frame(self):
        road_graph = _fetch_graph()
        assert road_graph.graph.graph["crs"] == "EPSG:4326"


class TestEndToEnd:
    def test_ai_enabled_prediction_persists_and_emits_event(self, monkeypatch):
        monkeypatch.setenv("SENTINEL_AI_ENABLED", "true")
        monkeypatch.setenv("ROAD_NETWORK_KEY", NETWORK_KEY)
        from app.config import get_settings

        get_settings.cache_clear()
        try:
            road_graph = _fetch_graph()
            edge_ids = _real_route_edge_ids(road_graph.graph)
            wkt = _route_linestring(road_graph.graph, edge_ids)

            with TestClient(app) as client:
                mission_id = client.post(
                    "/api/v1/missions", json={"objective": "AI end to end"}
                ).json()["id"]
                _seed_mission_and_route(mission_id, edge_ids, wkt)

                correlation_id = str(uuid4())
                response = client.post(
                    f"/api/v1/missions/{mission_id}/predictions",
                    json={
                        "prediction_type": "ETA",
                        "correlation_id": correlation_id,
                        "horizon_seconds": 300,
                    },
                )
                assert response.status_code == 201, response.text
                body = response.json()
                assert body["prediction_type"] == "ETA"
                assert body["correlation_id"] == correlation_id
                assert body["source"] == "sentinel_ai_road_graph"
                assert body["metadata"]["ai_source"] == "baseline_fallback"
                assert body["metadata"]["network_key"] == NETWORK_KEY

                # Persisted and retrievable.
                history = client.get(
                    f"/api/v1/missions/{mission_id}/predictions",
                    params={"prediction_type": "ETA"},
                )
                assert history.status_code == 200, history.text
                items = history.json()["items"]
                assert any(
                    item["prediction_id"] == body["prediction_id"] for item in items
                )

                # Event emitted with the same correlation id.
                events = client.get(f"/api/v1/missions/{mission_id}/events")
                assert events.status_code == 200, events.text
                prediction_events = [
                    event
                    for event in events.json()["items"]
                    if event["event_type"] == "PREDICTION_UPDATED"
                ]
                assert prediction_events, "no PREDICTION_UPDATED event was emitted"
                assert any(
                    event["payload"].get("correlation_id") == correlation_id
                    for event in prediction_events
                )
                assert any(
                    event["payload"].get("prediction_id") == body["prediction_id"]
                    for event in prediction_events
                )
        finally:
            get_settings.cache_clear()

    def test_ai_disabled_keeps_the_deterministic_source(self, monkeypatch):
        monkeypatch.setenv("SENTINEL_AI_ENABLED", "false")
        from app.config import get_settings

        get_settings.cache_clear()
        try:
            road_graph = _fetch_graph()
            edge_ids = _real_route_edge_ids(road_graph.graph)
            wkt = _route_linestring(road_graph.graph, edge_ids)

            with TestClient(app) as client:
                mission_id = client.post(
                    "/api/v1/missions", json={"objective": "AI disabled"}
                ).json()["id"]
                _seed_mission_and_route(mission_id, edge_ids, wkt)
                response = client.post(
                    f"/api/v1/missions/{mission_id}/predictions",
                    json={"prediction_type": "ETA"},
                )
                assert response.status_code == 201, response.text
                assert response.json()["source"] == "sentinel_prediction_engine"
        finally:
            get_settings.cache_clear()