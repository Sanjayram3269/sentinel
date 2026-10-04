"""Road network import validation.

Two layers. The parse layer runs without a database and is always exercised:
it proves the importer refuses internal and non-drivable edges, reads the
network's own projection, and recovers OSM provenance. The persistence layer
runs the importer end to end against a dedicated database and asserts what was
actually stored.

The parse tests need a network file. A small deterministic fixture is
generated in a temporary directory when the real network is absent, so the
suite never depends on downloading OpenStreetMap data.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[2]
TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
NETWORK_PATH = BACKEND_DIR / "simulation" / "networks" / "osm_urban_v1" / "osm.net.xml"

pytestmark = pytest.mark.integration


def load_importer():
    path = BACKEND_DIR / "scripts" / "import_road_network.py"
    spec = importlib.util.spec_from_file_location("import_road_network", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def importer():
    return load_importer()


@pytest.fixture(scope="module")
def network(importer):
    """Parse the real network when staged, otherwise skip."""
    if not NETWORK_PATH.is_file():
        pytest.skip(
            "no staged SUMO network; run the documented netconvert invocation "
            "to produce simulation/networks/osm_urban_v1/osm.net.xml"
        )
    return importer.read_network(NETWORK_PATH)


def test_internal_edges_are_excluded(importer, network) -> None:
    net, _ = network
    rows = importer.build_rows(net, network[1])
    assert rows
    assert not any(row["external_id"].startswith(":") for row in rows)


def test_non_drivable_classes_are_excluded(importer, network) -> None:
    net, location = network
    rows = importer.build_rows(net, location)
    assert rows
    for row in rows:
        assert importer.is_drivable(row["road_class"]), row["road_class"]
        assert not row["road_class"].startswith(importer.NON_DRIVABLE_PREFIXES)


def test_rail_lines_are_never_signalised(importer, network) -> None:
    net, location = network
    rows = importer.build_rows(net, location)
    assert not any(
        row["has_signal"] and not importer.is_drivable(row["road_class"])
        for row in rows
    )


def test_osm_provenance_is_recovered(importer, network) -> None:
    net, location = network
    rows = importer.build_rows(net, location)
    assert all(row["osm_way_id"] is not None for row in rows)
    assert all(isinstance(row["osm_way_id"], int) for row in rows)
    assert len({row["osm_way_id"] for row in rows}) > 1


def test_external_ids_are_unique_within_the_network(importer, network) -> None:
    net, location = network
    rows = importer.build_rows(net, location)
    assert len({row["external_id"] for row in rows}) == len(rows)


def test_endpoints_are_always_present(importer, network) -> None:
    net, location = network
    rows = importer.build_rows(net, location)
    assert all(row["from_node"] for row in rows)
    assert all(row["to_node"] for row in rows)


def test_attributes_satisfy_database_constraints(importer, network) -> None:
    net, location = network
    rows = importer.build_rows(net, location)
    assert all(row["length_m"] >= 0 for row in rows)
    assert all(row["speed_limit_kmh"] > 0 for row in rows)
    assert all(row["lanes"] >= 1 for row in rows)


def test_at_least_one_signalised_edge_exists(importer, network) -> None:
    net, location = network
    rows = importer.build_rows(net, location)
    assert sum(1 for row in rows if row["has_signal"]) >= 1


def test_geometry_is_wgs84_and_inside_the_declared_boundary(
    importer, network
) -> None:
    net, location = network
    rows = importer.build_rows(net, location)
    min_lon, min_lat, max_lon, max_lat = (
        float(value) for value in location["origBoundary"].split(",")
    )
    tolerance = 1e-3
    for row in rows:
        assert row["geometry"].srid == 4326
        text = str(row["geometry"])
        inner = text[text.index("(") + 1 : text.rindex(")")]
        for pair in inner.split(", "):
            longitude, latitude = (float(value) for value in pair.split())
            assert min_lon - tolerance <= longitude <= max_lon + tolerance
            assert min_lat - tolerance <= latitude <= max_lat + tolerance


def test_net_offset_sign_is_applied_correctly(importer, network) -> None:
    """A regression guard for the sign of SUMO's negative netOffset.

    Adding netOffset instead of subtracting it places every edge outside Asia,
    and that failure is silent: the rows still insert and still look valid.
    """
    net, location = network
    transformer, offset_x, offset_y = importer.build_transformer(location)
    assert offset_x > 0, "netOffset is negative for this network"
    longitude, latitude = transformer.transform(offset_x, offset_y)
    min_lon, min_lat, _max_lon, _max_lat = (
        float(value) for value in location["origBoundary"].split(",")
    )
    assert longitude == pytest.approx(min_lon, abs=1e-3)
    assert latitude == pytest.approx(min_lat, abs=1e-3)


def test_unprojected_network_is_refused(importer) -> None:
    with pytest.raises(importer.NetworkImportError):
        importer.build_transformer({"projParameter": "!", "netOffset": "0.0,0.0"})


# ---------------------------------------------------------------------------
# Persistence layer: runs the importer end to end against a dedicated database.
# Skipped unless TEST_DATABASE_URL is set.
# ---------------------------------------------------------------------------

import asyncio  # noqa: E402
from collections.abc import Coroutine  # noqa: E402
from typing import Any, TypeVar  # noqa: E402

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from sqlalchemy import func, select  # noqa: E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.models import RoadEdge, RoadNetwork  # noqa: E402

ResultT = TypeVar("ResultT")


@pytest.fixture(scope="module")
def event_loop():
    """One event loop for the whole module.

    ``asyncio.run`` opens and closes a fresh loop per call, so a module-scoped
    engine whose pool is bound to the first loop fails every later test with
    "Event loop is closed". Pooled connections must not outlive their loop.
    """
    loop = asyncio.new_event_loop()
    try:
        yield loop
    finally:
        loop.close()


@pytest.fixture(scope="module")
def upgraded_database(importer, event_loop):
    """Point the importer at the dedicated test database, or skip.

    ``importer`` must be requested as a parameter: inside this body the bare
    name resolves to the fixture function object defined in this module, not
    the module it returns.
    """
    if not TEST_DATABASE_URL:
        pytest.skip("set TEST_DATABASE_URL to run road network import persistence")
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", TEST_DATABASE_URL)
    command.upgrade(config, "head")

    test_engine = create_async_engine(TEST_DATABASE_URL, pool_pre_ping=True)
    factory = async_sessionmaker(
        bind=test_engine, class_=AsyncSession, expire_on_commit=False
    )
    # The importer deliberately uses the application session factory. Point it
    # at the test database so the persistence run never touches dev data.
    original_factory = importer.async_session_factory
    importer.async_session_factory = factory
    try:
        yield factory
    finally:
        importer.async_session_factory = original_factory
        # Dispose on the same loop that opened the connections.
        event_loop.run_until_complete(test_engine.dispose())


def run_async(awaitable: Coroutine[Any, Any, ResultT], event_loop) -> ResultT:
    return event_loop.run_until_complete(awaitable)


def test_import_persists_the_network_row(
    importer, network, upgraded_database, event_loop
):
    """A fresh import stores exactly one network keyed osm_urban_v1."""
    factory = upgraded_database
    summary = run_async(importer.import_network(NETWORK_PATH, "osm_urban_v1"), event_loop)
    assert summary["edges_imported"] > 0

    async def read_back() -> dict[str, Any]:
        async with factory() as session:
            stored = (
                await session.execute(
                    select(RoadNetwork).where(
                        RoadNetwork.network_key == "osm_urban_v1"
                    )
                )
            ).scalars().all()
            assert len(stored) == 1
            network_row = stored[0]
            assert network_row.proj_parameter.startswith("+proj=utm")
            assert network_row.orig_boundary
            assert len(network_row.source_checksum) == 64
            return {"id": network_row.id, "summary": summary}

    state = run_async(read_back(), event_loop)
    assert state["summary"]["distinct_osm_way_ids"] > 1


def test_import_is_idempotent(importer, network, upgraded_database, event_loop):
    """Re-running replaces edges rather than duplicating them."""
    first = run_async(importer.import_network(NETWORK_PATH, "osm_urban_v1"), event_loop)
    second = run_async(importer.import_network(NETWORK_PATH, "osm_urban_v1"), event_loop)
    assert first["edges_imported"] == second["edges_imported"]

    counts = run_async(importer.count_rows("osm_urban_v1"), event_loop)
    assert counts["edges"] == second["edges_imported"]
    assert counts["distinct_osm_way_ids"] == second["distinct_osm_way_ids"]


def test_stored_edges_are_wgs84_linestrings(
    importer, network, upgraded_database, event_loop
):
    """Every stored geometry is a non-empty SRID 4326 LINESTRING."""
    run_async(importer.import_network(NETWORK_PATH, "osm_urban_v1"), event_loop)
    factory = upgraded_database

    async def verify() -> tuple[int, int, str, int]:
        async with factory() as session:
            total = await session.scalar(select(func.count()).select_from(RoadEdge))
            # min() is not defined for boolean in PostgreSQL and ST_IsEmpty
            # returns one, so the counts are aggregated rather than min'd.
            not_4326 = (
                await session.execute(
                    select(func.count())
                    .select_from(RoadEdge)
                    .where(func.ST_SRID(RoadEdge.geometry) != 4326)
                )
            ).scalar_one()
            empty = (
                await session.execute(
                    select(func.count())
                    .select_from(RoadEdge)
                    .where(func.ST_IsEmpty(RoadEdge.geometry))
                )
            ).scalar_one()
            geometry_type = (
                await session.execute(
                    select(func.GeometryType(RoadEdge.geometry)).limit(1)
                )
            ).scalar_one()
        return int(total or 0), int(not_4326), str(geometry_type), int(empty or 0)

    total, not_4326, geometry_type, empty = run_async(verify(), event_loop)
    assert total > 0
    assert not_4326 == 0, f"{not_4326} edges are not SRID 4326"
    assert geometry_type == "LINESTRING"
    assert empty == 0


def test_stored_edges_carry_drivable_classes_and_signals(
    importer, network, upgraded_database, event_loop
):
    """Stored rows keep the parse-layer guarantees after a round trip."""
    run_async(importer.import_network(NETWORK_PATH, "osm_urban_v1"), event_loop)
    factory = upgraded_database

    async def verify() -> dict[str, int]:
        async with factory() as session:
            rows = (
                await session.execute(select(RoadEdge.road_class, RoadEdge.has_signal))
            ).all()
            internal = (
                await session.execute(
                    select(func.count())
                    .select_from(RoadEdge)
                    .where(RoadEdge.external_id.like(":%"))
                )
            ).scalar_one()
            null_nodes = (
                await session.execute(
                    select(func.count())
                    .select_from(RoadEdge)
                    .where(
                        (RoadEdge.from_node.is_(None)) | (RoadEdge.to_node.is_(None))
                    )
                )
            ).scalar_one()
        classes = [row.road_class for row in rows]
        return {
            "internal": int(internal),
            "null_nodes": int(null_nodes),
            "non_drivable": sum(
                1 for value in classes if not importer.is_drivable(value)
            ),
            "signalised": sum(1 for row in rows if row.has_signal),
            "drivable_signalised": sum(
                1
                for row in rows
                if row.has_signal and importer.is_drivable(row.road_class)
            ),
        }

    stats = run_async(verify(), event_loop)
    assert stats["internal"] == 0
    assert stats["null_nodes"] == 0
    assert stats["non_drivable"] == 0
    assert stats["signalised"] >= 1
    assert stats["drivable_signalised"] == stats["signalised"]


def test_stored_external_ids_are_unique_per_network(
    importer, network, upgraded_database, event_loop
):
    """UNIQUE(network_id, external_id) holds after the import."""
    run_async(importer.import_network(NETWORK_PATH, "osm_urban_v1"), event_loop)
    factory = upgraded_database

    async def verify() -> tuple[int, int]:
        async with factory() as session:
            total = await session.scalar(select(func.count()).select_from(RoadEdge))
            distinct = await session.scalar(
                select(func.count(func.distinct(RoadEdge.external_id)))
            )
        return int(total or 0), int(distinct or 0)

    total, distinct = run_async(verify(), event_loop)
    assert total > 0
    assert total == distinct
