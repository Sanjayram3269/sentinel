"""Road-graph access for the AI layer.

Two problems are solved here. First, the AI library needs a NetworkX graph and
the database needs SQLAlchemy; keeping the conversion in one place means the AI
library never imports the application. Second, rebuilding a 712-edge graph on
every prediction would put real work in the request path, so the built graph is
cached per process and per network key and invalidated explicitly.

The cache is keyed by network key *and* the network's ``created_at``, so a
re-import of the OSM data is picked up rather than silently serving the stale
graph for the lifetime of the process.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Optional

import networkx as nx
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.road_graph import load_edges, load_network

__all__ = [
    "AiRoadGraph",
    "RoadNetworkUnavailable",
    "clear_road_graph_cache",
    "get_road_graph",
]


class RoadNetworkUnavailable(RuntimeError):
    """No imported network is available to reason about."""


@dataclass(frozen=True)
class AiRoadGraph:
    """A graph plus the identity of the network it was built from."""

    network_key: str
    graph: nx.DiGraph
    edge_count: int

    @property
    def node_count(self) -> int:
        return self.graph.number_of_nodes()


# Cache is process-wide; a single entry per network key.
_CACHE: dict[str, tuple[object, AiRoadGraph]] = {}
_CACHE_LOCK: Optional[asyncio.Lock] = None
_CACHE_LOCK_LOOP: Optional[asyncio.AbstractEventLoop] = None


def _lock() -> asyncio.Lock:
    """Return the cache lock bound to the *running* loop.

    A module-level ``asyncio.Lock`` created before any loop exists would bind
    to whichever loop first awaited it, and later fail on a different loop. The
    lock is therefore created per loop.
    """
    global _CACHE_LOCK, _CACHE_LOCK_LOOP
    loop = asyncio.get_running_loop()
    if _CACHE_LOCK is None or _CACHE_LOCK_LOOP is not loop:
        _CACHE_LOCK = asyncio.Lock()
        _CACHE_LOCK_LOOP = loop
    return _CACHE_LOCK


def clear_road_graph_cache() -> None:
    """Drop every cached graph. Used by tests and after a re-import."""
    _CACHE.clear()


async def get_road_graph(
    db: AsyncSession, network_key: str
) -> AiRoadGraph:
    """Return the AI graph for ``network_key``, building it at most once.

    Raises :class:`RoadNetworkUnavailable` when the operator has not imported
    that network, so callers fall back explicitly instead of reasoning about a
    graph that does not exist.
    """
    from sentinel_ai.world.city_graph import build_graph_from_road_edges

    async with _lock():
        cached = _CACHE.get(network_key)
        if cached is not None:
            stamp, graph = cached
            current = await _network_stamp(db, network_key)
            if current is not None and current == stamp:
                return graph

        stamp = await _network_stamp(db, network_key)
        if stamp is None:
            _CACHE.pop(network_key, None)
            raise RoadNetworkUnavailable(
                f"no road network is imported for key {network_key!r}; run "
                "scripts/import_road_network.py to create it"
            )

        network = await load_network(db, network_key)
        edges = await load_edges(db, network)
        graph = build_graph_from_road_edges(edges)
        built = AiRoadGraph(
            network_key=network_key,
            graph=graph,
            edge_count=graph.number_of_edges(),
        )
        _CACHE[network_key] = (stamp, built)
        return built


async def _network_stamp(
    db: AsyncSession, network_key: str
) -> Optional[tuple[str, object]]:
    """Identify the network cheaply, for cache invalidation."""
    from app.models.road import RoadNetwork
    from sqlalchemy import select

    row = (
        await db.execute(
            select(
                RoadNetwork.id,
                RoadNetwork.source_checksum,
                RoadNetwork.created_at,
            ).where(RoadNetwork.network_key == network_key)
        )
    ).first()
    if row is None:
        return None
    return (str(row.id), row.source_checksum, row.created_at)