"""Resolve real node coordinates for route reasoning.

Node positions are read from the graph itself. The previous implementation
parsed identifiers such as ``N_3_7`` and multiplied by a fixed 200 m grid
spacing, which only ever described the synthetic test city: a real OSM node id
contains no grid indices, so the parse either raised or produced nonsense, and
two of the eleven prediction features depended on it.

A graph that does not carry coordinates is a programming error, not a
condition to paper over. ``node_position`` therefore raises rather than
falling back to a fabricated position, so a caller cannot silently reason
about the wrong geography.

This module also owns *units*. Coordinates arrive in one of two frames: the
imported network stores WGS84 degrees on ``x``/``y``, while the synthetic city
stores planar metres. Comparing a degree value against a threshold expressed in
metres silently inverts the test -- a hazard radius of 100 m exceeds every
inter-node distance in a city whose coordinates span a few thousandths of a
degree, so every edge reads as inside the hazard. ``distance_m`` therefore
dispatches on the frame the graph declares and always returns metres.
"""

from __future__ import annotations

import math
from typing import Any

__all__ = [
    "PLANAR_CRS",
    "WGS84_CRS",
    "node_position",
    "is_wgs84",
    "distance_m",
]

#: Coordinate frame declared by a graph. Unmarked graphs are treated as planar
#: metres, which is the historical behaviour of this library.
PLANAR_CRS = "PLANAR_METRES"

#: Coordinate frame for imported road networks: x = longitude, y = latitude.
WGS84_CRS = "EPSG:4326"

_EARTH_RADIUS_M = 6_371_008.8


def is_wgs84(graph: Any) -> bool:
    """True when the graph's coordinates are WGS84 degrees."""
    try:
        return graph.graph.get("crs") == WGS84_CRS
    except AttributeError:
        return False


def distance_m(
    graph: Any, first: tuple[float, float], second: tuple[float, float]
) -> float:
    """Return the distance between two positions in **metres**.

    Uses the haversine formula for a graph declaring WGS84 coordinates and
    plain Euclidean distance for a planar graph, so a caller may compare the
    result against a metre-denominated threshold in either case.
    """
    x1, y1 = first
    x2, y2 = second
    if not is_wgs84(graph):
        return math.hypot(x2 - x1, y2 - y1)

    lon1, lat1, lon2, lat2 = map(math.radians, (x1, y1, x2, y2))
    d_lon = lon2 - lon1
    d_lat = lat2 - lat1
    a = math.sin(d_lat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(d_lon / 2) ** 2
    return 2 * _EARTH_RADIUS_M * math.asin(math.sqrt(a))


def node_position(graph: Any, node_id: str) -> tuple[float, float]:
    """Return ``(x, y)`` for a node, in whatever frame the graph uses.

    Real networks carry WGS84 degrees on ``x``/``y``. The synthetic city keeps
    its own planar metres on the same keys, so both worlds are supported by
    reading attributes rather than by reconstructing positions from an id.
    """
    try:
        data = graph.nodes[node_id]
    except (KeyError, TypeError) as error:
        raise KeyError(
            f"node {node_id!r} is not present in the graph; a route cannot be "
            "reasoned about without a real topology"
        ) from error
    missing = [axis for axis in ("x", "y") if axis not in data]
    if missing:
        raise KeyError(
            f"node {node_id!r} has no {' and '.join(missing)} attribute; build "
            "the graph from road edges so nodes carry real coordinates"
        )
    return float(data["x"]), float(data["y"])
