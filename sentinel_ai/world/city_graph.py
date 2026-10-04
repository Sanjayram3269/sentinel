import networkx as nx
import numpy as np
import random
from typing import Any, Iterable, Tuple

from sentinel_ai.world.geometry import PLANAR_CRS, WGS84_CRS

#: Spacing of the synthetic grid, in metres. Synthetic world only.
GRID_SPACING_M = 200


def build_city_graph(seed: int = 42) -> nx.DiGraph:
    """Builds a synthetic city graph (perturbed grid + arterials + ring road)."""
    np.random.seed(seed)
    random.seed(seed)

    # 10x10 grid
    G = nx.grid_2d_graph(10, 10, create_using=nx.DiGraph)

    # Add reverse edges to make it a fully bidirectional grid
    reverse_edges = [(v, u) for u, v in G.edges()]
    G.add_edges_from(reverse_edges)

    # Map (i,j) nodes to string IDs
    mapping = {node: f"N_{node[0]}_{node[1]}" for node in G.nodes()}
    nx.relabel_nodes(G, mapping, copy=False)

    # Add attributes to edges
    for u, v, data in G.edges(data=True):
        data["edge_id"] = f"{u}->{v}"

        # Base grid edges are local roads
        data["road_class"] = "local"
        data["speed_limit_kmh"] = 30.0
        data["lanes"] = 1
        data["length_m"] = float(np.random.uniform(150, 250))
        data["has_signal"] = False

        # Some are arterials (e.g., column 3 and 7, row 3 and 7)
        u_parts = u.split("_")
        v_parts = v.split("_")
        u_x, u_y = int(u_parts[1]), int(u_parts[2])
        v_x, v_y = int(v_parts[1]), int(v_parts[2])

        is_arterial = (u_x in (3, 7) and v_x in (3, 7)) or (u_y in (3, 7) and v_y in (3, 7))

        if is_arterial:
            data["road_class"] = "arterial"
            data["speed_limit_kmh"] = 50.0
            data["lanes"] = 2
            data["has_signal"] = True

        # Ring road on the perimeter
        is_ring = (u_x in (0, 9) and v_x in (0, 9)) or (u_y in (0, 9) and v_y in (0, 9))
        if is_ring:
            data["road_class"] = "highway"
            data["speed_limit_kmh"] = 80.0
            data["lanes"] = 3
            data["has_signal"] = False

    # Nodes carry explicit coordinates so one reasoning path serves both
    # worlds. Positions used to be reconstructed from the "N_x_y" identifier,
    # which only ever described this grid.
    for node in G.nodes():
        grid_x, grid_y = int(node.split("_")[1]), int(node.split("_")[2])
        G.nodes[node]["x"] = float(grid_x * GRID_SPACING_M)
        G.nodes[node]["y"] = float(grid_y * GRID_SPACING_M)

    # Declared so distance helpers know these are planar metres, not degrees.
    G.graph["crs"] = PLANAR_CRS
    return G


def build_graph_from_road_edges(edges: Iterable[Any]) -> "nx.DiGraph":
    """Adapt backend road-edge records into a graph for the AI library.

    This is the real-network counterpart to :func:`build_city_graph`. The
    backend adapter owns the database and the projection, so this function
    accepts already-loaded records and performs no I/O and no coordinate
    conversion of its own. It deliberately imports nothing from the FastAPI
    application.

    Each record must expose ``from_node``, ``to_node``, ``length_m``,
    ``speed_limit_kmh``, ``road_class``, ``lanes`` and ``has_signal``, plus
    ``start_lon``/``start_lat``/``end_lon``/``end_lat``.
    ``app.services.road_graph.RoadGraphEdge`` satisfies that shape.
    """
    graph = nx.DiGraph()
    # Imported road networks carry WGS84 degrees, and the frame is declared so
    # distance helpers convert to metres instead of comparing degrees against
    # metre thresholds.
    graph.graph["crs"] = WGS84_CRS
    for edge in edges:
        graph.add_node(
            edge.from_node, x=float(edge.start_lon), y=float(edge.start_lat)
        )
        graph.add_node(edge.to_node, x=float(edge.end_lon), y=float(edge.end_lat))
        graph.add_edge(
            edge.from_node,
            edge.to_node,
            length_m=float(edge.length_m),
            speed_limit_kmh=float(edge.speed_limit_kmh),
            road_class=str(edge.road_class),
            lanes=int(edge.lanes),
            has_signal=bool(edge.has_signal),
            road_edge_id=str(getattr(edge, "road_edge_id", "")),
            external_id=str(getattr(edge, "external_id", "")),
        )
    return graph


def get_free_flow_time_s(length_m: float, speed_limit_kmh: float) -> float:
    speed_ms = speed_limit_kmh * 1000 / 3600
    return length_m / speed_ms
