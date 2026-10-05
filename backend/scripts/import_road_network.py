"""Import a SUMO ``.net.xml`` into the persistent road graph.

This is an operator-run importer. It is never invoked by Alembic, by
application startup, or by any test fixture that touches a shared database.
Run it explicitly:

    python scripts/import_road_network.py --net simulation/networks/osm_urban_v1/osm.net.xml

The importer reads the network's own projection metadata instead of
re-projecting it. A SUMO network built from OSM stores a ``projParameter``
proj-string plus a ``netOffset``, and edge shapes are expressed in that
projected frame. Geometry is therefore converted forward from projected
metres to WGS84 and stored as SRID 4326, which is the only geometry the
backend persists. Regenerating the network with ``--proj.plain-geo`` would be
wrong: the network is already correctly projected, and plain-geo is intended
for output writers rather than for simulation.

Only drivable edges are imported. Internal junction edges, whose identifiers
begin with a colon, are simulator bookkeeping rather than roads, and
non-drivable classes are excluded so that a railway passing beside a
signalised junction is never treated as a signalised approach.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import argparse  # noqa: E402
import asyncio  # noqa: E402
import hashlib  # noqa: E402
from typing import Any, Iterator  # noqa: E402

import sumolib.net  # noqa: E402
from geoalchemy2 import WKTElement  # noqa: E402
from pyproj import Transformer  # noqa: E402
from sqlalchemy import delete, func, select  # noqa: E402

from app.db.session import async_session_factory, engine  # noqa: E402
from app.models import RoadEdge, RoadNetwork  # noqa: E402

NETWORK_SOURCE = "sumo_netconvert"

# Edge classes no response vehicle can drive on. Excluding them keeps the
# routing graph drivable and keeps rail lines out of the signalised set.
NON_DRIVABLE_PREFIXES = (
    "railway",
    "highway.footway",
    "highway.cycleway",
    "highway.steps",
    "highway.pedestrian",
)


class NetworkImportError(RuntimeError):
    """Raised when a network cannot be interpreted safely."""


def is_drivable(road_class: str) -> bool:
    """Return whether an edge class can carry a response vehicle."""
    return not road_class.startswith(NON_DRIVABLE_PREFIXES)


def build_transformer(location: dict[str, Any]) -> tuple[Transformer, float, float]:
    """Build a projected-metres to WGS84 converter from network metadata."""
    proj_parameter = location.get("projParameter") or ""
    if not proj_parameter or proj_parameter == "!":
        raise NetworkImportError(
            "network declares no projection; import a network built from a "
            "geographic source such as OSM"
        )
    net_offset = location.get("netOffset") or "0.0,0.0"
    # SUMO stores netOffset as the value subtracted from the projected frame
    # to produce network-local coordinates, so absolute = local - netOffset.
    # For this network netOffset is negative, and adding it instead would place
    # every edge outside Asia entirely.
    offset_x, offset_y = (-float(value) for value in net_offset.split(","))
    transformer = Transformer.from_proj(proj_parameter, "EPSG:4326", always_xy=True)
    return transformer, offset_x, offset_y


def to_wkt_linestring(
    shape: list[tuple[float, float]],
    transformer: Transformer,
    offset_x: float,
    offset_y: float,
    srid: int = 4326,
) -> WKTElement:
    """Convert a SUMO edge shape into a WGS84 LINESTRING."""
    if len(shape) < 2:
        raise NetworkImportError("edge shape has fewer than two vertices")
    lon, lat = transformer.transform(
        [point[0] + offset_x for point in shape],
        [point[1] + offset_y for point in shape],
    )
    pairs = ", ".join(f"{x!r} {y!r}" for x, y in zip(lon, lat))
    return WKTElement(f"LINESTRING({pairs})", srid=srid)


def iter_road_edges(net: sumolib.net.Net) -> Iterator[tuple[Any, bool]]:
    """Yield ``(edge, has_signal)`` for each drivable, non-internal edge."""
    for edge in net.getEdges():
        if edge.getID().startswith(":"):
            continue
        if not is_drivable(edge.getType() or ""):
            continue
        yield edge, bool(edge.getTLS())


def build_rows(
    net: sumolib.net.Net, location: dict[str, Any]
) -> list[dict[str, Any]]:
    """Translate a parsed network into insertable row dictionaries."""
    transformer, offset_x, offset_y = build_transformer(location)
    rows: list[dict[str, Any]] = []
    for edge, has_signal in iter_road_edges(net):
        raw_way = edge.getLane(0).getParams().get("origId")
        # netconvert records several OSM ways in one origId when tls.join or
        # junctions.join merges them, for example "1055153853 1055153851".
        # The first identifier is the primary way; the edge cannot belong to
        # every contributing way, so provenance keeps the leading id.
        first_way = str(raw_way).split()[0] if raw_way else ""
        try:
            osm_way_id = int(first_way) if first_way else None
        except ValueError:
            osm_way_id = None
        speed = float(edge.getSpeed())
        if speed <= 0:
            raise NetworkImportError(
                f"edge {edge.getID()} declares a non-positive speed"
            )
        lanes = int(edge.getLaneNumber())
        if lanes < 1:
            raise NetworkImportError(f"edge {edge.getID()} declares no lanes")
        rows.append(
            {
                "source": NETWORK_SOURCE,
                "external_id": edge.getID(),
                "osm_way_id": osm_way_id,
                "from_node": edge.getFromNode().getID(),
                "to_node": edge.getToNode().getID(),
                "geometry": to_wkt_linestring(
                    edge.getRawShape(), transformer, offset_x, offset_y
                ),
                "length_m": float(edge.getLength()),
                "speed_limit_kmh": speed * 3.6,
                "road_class": edge.getType(),
                "lanes": lanes,
                "has_signal": has_signal,
            }
        )
    if not rows:
        raise NetworkImportError("no drivable edges were found in the network")
    return rows


def read_network(path: Path) -> tuple[sumolib.net.Net, dict[str, Any]]:
    """Parse a network file without internal edges."""
    if not path.is_file():
        raise NetworkImportError(f"network file not found: {path}")
    net = sumolib.net.readNet(str(path), withInternal=False)
    return net, net._location  # noqa: SLF001 - no public accessor exists


async def import_network(net_path: Path, network_key: str) -> dict[str, Any]:
    """Replace one network's edges inside a single transaction.

    Re-running is safe: the existing rows are deleted before the new set is
    written, so an edge dropped from the source network cannot survive, and
    the network row keeps its identity across runs.
    """
    net, location = read_network(net_path)
    rows = build_rows(net, location)
    checksum = hashlib.sha256(net_path.read_bytes()).hexdigest()
    proj_parameter = location.get("projParameter") or ""
    orig_boundary = location.get("origBoundary") or ""

    async with async_session_factory() as session:
        async with session.begin():
            network = await session.scalar(
                select(RoadNetwork).where(RoadNetwork.network_key == network_key)
            )
            if network is None:
                network = RoadNetwork(
                    network_key=network_key,
                    proj_parameter=proj_parameter,
                    orig_boundary=orig_boundary,
                    source_checksum=checksum,
                )
                session.add(network)
                await session.flush()
            else:
                await session.execute(
                    delete(RoadEdge).where(RoadEdge.network_id == network.id)
                )
                network.proj_parameter = proj_parameter
                network.orig_boundary = orig_boundary
                network.source_checksum = checksum

            session.add_all([RoadEdge(network_id=network.id, **row) for row in rows])

    return {
        "network_key": network_key,
        "edges_imported": len(rows),
        "distinct_osm_way_ids": len({row["osm_way_id"] for row in rows}),
        "signalised_edges": sum(1 for row in rows if row["has_signal"]),
        "road_classes": len({row["road_class"] for row in rows}),
        "source_checksum": checksum[:16],
        "orig_boundary": orig_boundary,
    }


async def count_rows(network_key: str) -> dict[str, int]:
    """Report stored counts for one network."""
    async with async_session_factory() as session:
        edges = await session.scalar(
            select(func.count())
            .select_from(RoadEdge)
            .join(RoadNetwork, RoadEdge.network_id == RoadNetwork.id)
            .where(RoadNetwork.network_key == network_key)
        )
        ways = await session.scalar(
            select(func.count(func.distinct(RoadEdge.osm_way_id)))
            .select_from(RoadEdge)
            .join(RoadNetwork, RoadEdge.network_id == RoadNetwork.id)
            .where(RoadNetwork.network_key == network_key)
        )
    return {"edges": int(edges or 0), "distinct_osm_way_ids": int(ways or 0)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Import a SUMO network.")
    parser.add_argument("--net", required=True, type=Path, help="path to a .net.xml")
    parser.add_argument("--network-key", default="osm_urban_v1")
    parser.add_argument("--verify", action="store_true", help="only report stored rows")
    arguments = parser.parse_args()

    async def run() -> dict[str, Any]:
        # The engine must be disposed on the same event loop that opened its
        # connections. Disposing from a second asyncio.run() closes pooled
        # connections on a loop that has already shut down, which raises out of
        # asyncpg's own close() and buries the real result in a traceback.
        try:
            if arguments.verify:
                return await count_rows(arguments.network_key)
            summary = await import_network(arguments.net, arguments.network_key)
            summary.update(await count_rows(arguments.network_key))
            return summary
        finally:
            await engine.dispose()

    try:
        summary = asyncio.run(run())
    except NetworkImportError as error:
        print(f"import failed: {error}", file=sys.stderr)
        return 1

    for key, value in summary.items():
        print(f"{key:24} {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
