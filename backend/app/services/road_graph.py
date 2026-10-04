"""Build a NetworkX graph from persisted road edges.

The routing graph is derived from the database rather than fabricated, so
routing, prediction and the map all describe the same streets. The attribute
names published on each edge are fixed by the AI library, which reads
``length_m``, ``speed_limit_kmh``, ``road_class``, ``lanes`` and
``has_signal`` as plain subscripts; renaming any of them would raise KeyError
at inference time rather than degrade gracefully.

Nodes carry ``x`` and ``y`` in WGS84 degrees. The AI library needs real node
coordinates for incident proximity and hazard exposure, and previously
synthesised them from a grid identifier, which cannot work for a real network.

Endpoint coordinates are read from PostGIS as scalars using
``ST_StartPoint``/``ST_EndPoint`` rather than by indexing the geometry object.
GeoAlchemy2 0.17 exposes no subscript or shape conversion on the element it
returns for a read, and Shapely is not a project dependency, so the scalar
route is the one that works without adding an undeclared package.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import RoadEdge, RoadNetwork

#: Attribute names the AI library requires on every edge.
REQUIRED_EDGE_ATTRIBUTES = (
    "length_m",
    "speed_limit_kmh",
    "road_class",
    "lanes",
    "has_signal",
)


class RoadNetworkNotFound(LookupError):
    """Raised when no imported network matches the requested key."""


@dataclass(frozen=True)
class RoadGraphEdge:
    """One edge reduced to exactly what the routing graph publishes."""

    road_edge_id: str
    external_id: str
    osm_way_id: int | None
    from_node: str
    to_node: str
    start_lon: float
    start_lat: float
    end_lon: float
    end_lat: float
    length_m: float
    speed_limit_kmh: float
    road_class: str
    lanes: int
    has_signal: bool


def _start_longitude() -> Any:
    return func.ST_X(func.ST_StartPoint(RoadEdge.geometry))


def _start_latitude() -> Any:
    return func.ST_Y(func.ST_StartPoint(RoadEdge.geometry))


def _end_longitude() -> Any:
    return func.ST_X(func.ST_EndPoint(RoadEdge.geometry))


def _end_latitude() -> Any:
    return func.ST_Y(func.ST_EndPoint(RoadEdge.geometry))


async def load_network(db: AsyncSession, network_key: str) -> RoadNetwork:
    """Return the imported network or raise ``RoadNetworkNotFound``."""
    network = await db.scalar(
        select(RoadNetwork).where(RoadNetwork.network_key == network_key)
    )
    if network is None:
        raise RoadNetworkNotFound(
            f"no road network is imported for key {network_key!r}; run "
            "scripts/import_road_network.py to create it"
        )
    return network


async def load_edges(
    db: AsyncSession, network: RoadNetwork
) -> list[RoadGraphEdge]:
    """Read one network's edges together with their endpoint coordinates."""
    statement = (
        select(
            RoadEdge.id,
            RoadEdge.external_id,
            RoadEdge.osm_way_id,
            RoadEdge.from_node,
            RoadEdge.to_node,
            _start_longitude().label("start_lon"),
            _start_latitude().label("start_lat"),
            _end_longitude().label("end_lon"),
            _end_latitude().label("end_lat"),
            RoadEdge.length_m,
            RoadEdge.speed_limit_kmh,
            RoadEdge.road_class,
            RoadEdge.lanes,
            RoadEdge.has_signal,
        )
        .where(RoadEdge.network_id == network.id)
        .order_by(RoadEdge.external_id)
    )
    rows = (await db.execute(statement)).all()
    return [
        RoadGraphEdge(
            road_edge_id=str(row.id),
            external_id=row.external_id,
            osm_way_id=row.osm_way_id,
            from_node=row.from_node,
            to_node=row.to_node,
            start_lon=float(row.start_lon),
            start_lat=float(row.start_lat),
            end_lon=float(row.end_lon),
            end_lat=float(row.end_lat),
            length_m=float(row.length_m),
            speed_limit_kmh=float(row.speed_limit_kmh),
            road_class=row.road_class,
            lanes=int(row.lanes),
            has_signal=bool(row.has_signal),
        )
        for row in rows
    ]


def edge_attributes(edge: RoadGraphEdge) -> dict[str, Any]:
    """Publish one edge under the attribute names the AI library expects."""
    return {
        "length_m": edge.length_m,
        "speed_limit_kmh": edge.speed_limit_kmh,
        "road_class": edge.road_class,
        "lanes": edge.lanes,
        "has_signal": edge.has_signal,
        # Retained so a routing provider can translate between the canonical
        # SENTINEL identifier and the simulator's edge id without re-querying.
        "road_edge_id": edge.road_edge_id,
        "external_id": edge.external_id,
        "osm_way_id": edge.osm_way_id,
    }


def build_graph_from_edges(edges: Iterable[RoadGraphEdge]) -> Any:
    """Build a directed graph from road edges, carrying node coordinates."""
    import networkx as nx

    graph = nx.DiGraph()
    for edge in edges:
        graph.add_node(
            edge.from_node, x=edge.start_lon, y=edge.start_lat, osm_id=edge.from_node
        )
        graph.add_node(
            edge.to_node, x=edge.end_lon, y=edge.end_lat, osm_id=edge.to_node
        )
        graph.add_edge(edge.from_node, edge.to_node, **edge_attributes(edge))
    return graph


async def build_networkx_graph(db: AsyncSession, network_key: str) -> Any:
    """Load one imported network and return it as a NetworkX directed graph."""
    network = await load_network(db, network_key)
    return build_graph_from_edges(await load_edges(db, network))
