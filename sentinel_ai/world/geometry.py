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
"""

from __future__ import annotations

from typing import Any

__all__ = ["node_position"]


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
