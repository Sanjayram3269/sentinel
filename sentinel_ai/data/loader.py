"""Dataset construction over a real road network.

The previous ``load_dataset`` raised ``NotImplementedError`` for
``source="sumo"``, which left the AI layer permanently bound to the synthetic
10x10 grid. This module implements the real path.

Two limitations are stated plainly, because they bound what the resulting
dataset may be used to claim:

* Labels are produced by the deterministic traffic simulator in
  :mod:`sentinel_ai.world.simulator`, not replayed from observed SUMO trip
  output. No trip-output recordings are committed to this repository, so no
  accuracy figure may be quoted from a dataset built here. Treat the labels as
  a reproducible proxy suitable for development and regression work only.
* Nodes, edges, lengths, speeds, lanes and signals are read from the graph the
  caller supplies. When that graph is built from imported PostGIS road edges it
  is the real Bengaluru network; when it is the synthetic city it is not.

Rows are returned as plain dicts rather than a ``pandas.DataFrame``. pandas is
not a project dependency and adding it purely to hold an intermediate table
would be a large dependency for no analytic benefit. Column names and the
feature contract are unchanged.
"""

from __future__ import annotations

import random
from itertools import islice
from typing import Any, Dict, List, Optional, Sequence

import networkx as nx

from sentinel_ai.contracts import (
    HazardState,
    IncidentState,
    UnitState,
    WorldState,
)
from sentinel_ai.prediction.baselines import baseline_free_flow_eta
from sentinel_ai.prediction.features import compute_route_features
from sentinel_ai.world.simulator import execute_route

__all__ = [
    "DEFAULT_FAILURE_MARGIN_MIN",
    "build_network_scenario",
    "k_shortest_paths",
    "load_dataset",
]

#: Extra travel time, in minutes, beyond the current-speed estimate before a
#: route counts as failed.
DEFAULT_FAILURE_MARGIN_MIN = 5.0

#: Fixed epoch base so datasets are reproducible: 2023-01-01T00:00:00Z.
_EPOCH_BASE = 1_672_531_200.0

#: Stored value for "route never completed", matching the original contract.
_UNREACHABLE_MIN = 999.0

SCENARIO_TYPES = ("mixed", "accident", "hazard", "closure")


def k_shortest_paths(
    graph: nx.DiGraph, source: Any, target: Any, k: int, weight: str = "length_m"
) -> List[List[Any]]:
    """Return up to ``k`` paths, or an empty list when none exist."""
    try:
        return list(islice(nx.shortest_simple_paths(graph, source, target, weight=weight), k))
    except (nx.NetworkXNoPath, nx.NodeNotFound):
        return []


def build_network_scenario(
    scenario_id: str,
    seed: int,
    graph: nx.DiGraph,
    scenario_type: str = "mixed",
) -> WorldState:
    """Build a reproducible scenario positioned on this graph's real nodes.

    Node identifiers come from the graph rather than from a hardcoded grid, so
    incident and hazard positions are genuine network nodes and the distance
    features computed from them are metres on real geography.
    """
    if scenario_type not in SCENARIO_TYPES:
        raise ValueError(
            f"scenario_type must be one of {SCENARIO_TYPES}, got {scenario_type!r}"
        )

    rng = random.Random(seed)
    nodes = sorted(graph.nodes())
    if not nodes:
        raise ValueError("cannot build a scenario for a graph with no nodes")

    def pick() -> Any:
        return rng.choice(nodes)

    incidents = [
        IncidentState(
            incident_id=f"INC_{scenario_id}_1",
            node=pick(),
            priority=rng.randint(1, 3),
            required_responder_types=["ambulance", "police"],
        )
    ]
    hazards: List[HazardState] = []
    if scenario_type in {"hazard", "mixed"}:
        hazards.append(
            HazardState(
                hazard_id=f"HAZ_{scenario_id}_1",
                center_node=pick(),
                radius_m=rng.uniform(100.0, 300.0),
                expansion_rate_m_per_s=rng.uniform(0.1, 0.5),
            )
        )

    closed_edges: List[str] = []
    if scenario_type in {"closure", "mixed"}:
        # Choose uniformly among real directed edges. Scanning in iteration
        # order and accepting the first near-random hit instead picked whatever
        # edge happened to come first -- typically a hub -- which made most
        # sampled routes fail and skewed the whole dataset.
        edge_list = list(graph.edges())
        if edge_list:
            source_node, target_node = edge_list[rng.randrange(len(edge_list))]
            closed_edges.append(f"{source_node}->{target_node}")

    return WorldState(
        timestamp_utc=_EPOCH_BASE + rng.uniform(0.0, 86400.0),
        demand_level=rng.uniform(0.5, 1.5),
        closed_edges=closed_edges,
        incidents=incidents,
        hazards=hazards,
        units=[
            UnitState(
                unit_id=f"U{i}", unit_type=kind, position=pick(), available=True
            )
            for i, kind in enumerate(("ambulance", "fire", "police"), start=1)
        ],
        hospitals=[],
    )


def load_dataset(
    source: str = "synthetic",
    *,
    graph: Optional[nx.DiGraph] = None,
    num_scenarios: int = 50,
    start_seed: int = 100,
    od_pairs_per_scenario: int = 10,
    paths_per_pair: int = 3,
    failure_margin_min: float = DEFAULT_FAILURE_MARGIN_MIN,
) -> List[Dict[str, Any]]:
    """Build a labelled route dataset.

    ``source="sumo"`` consumes the real imported network and is the path SENTINEL
    uses. It requires a ``graph``; without one this raises rather than quietly
    falling back to the synthetic city, which would label real road features
    with a synthetic origin.

    Returns one dict per route sample carrying the AI feature contract columns
    plus the labels ``actual_eta_min`` (minutes) and ``route_fails`` (0/1).
    """
    if source not in {"sumo", "synthetic"}:
        raise ValueError("source must be 'sumo' or 'synthetic'")
    if graph is None:
        raise ValueError(
            "a road graph is required; build one from imported road edges via "
            "sentinel_ai.world.city_graph.build_graph_from_road_edges"
        )

    nodes = sorted(graph.nodes())
    if len(nodes) < 2:
        raise ValueError("the road graph needs at least two nodes to sample routes")

    rows: List[Dict[str, Any]] = []
    for index in range(num_scenarios):
        seed = start_seed + index
        rng = random.Random(seed)
        scenario_type = SCENARIO_TYPES[seed % len(SCENARIO_TYPES)]
        world_state = build_network_scenario(f"TRAIN_{index}", seed, graph, scenario_type)

        for _ in range(od_pairs_per_scenario):
            origin, destination = rng.choice(nodes), rng.choice(nodes)
            if origin == destination:
                continue

            for path in k_shortest_paths(graph, origin, destination, k=paths_per_pair):
                route_edges = [
                    f"{path[position]}->{path[position + 1]}"
                    for position in range(len(path) - 1)
                ]
                if not route_edges:
                    continue

                features = compute_route_features(
                    graph, route_edges, world_state.timestamp_utc, world_state
                )
                free_flow_eta = baseline_free_flow_eta(graph, route_edges)
                actual_eta = execute_route(
                    graph, route_edges, world_state.timestamp_utc, world_state, seed=seed
                )

                mean_speed = features.get("mean_current_speed", 0.0)
                if mean_speed <= 0:
                    current_speed_eta = free_flow_eta
                else:
                    current_speed_eta = (
                        features.get("length", 0.0) / (mean_speed * 1000.0 / 3600.0)
                    ) / 60.0

                unreachable = actual_eta == float("inf")
                route_fails = unreachable or actual_eta > max(
                    1.5 * current_speed_eta, current_speed_eta + failure_margin_min
                )

                row: Dict[str, Any] = {
                    "scenario_id": f"TRAIN_{index}",
                    "scenario_type": scenario_type,
                    "seed": seed,
                    "actual_eta_min": (
                        _UNREACHABLE_MIN if unreachable else actual_eta
                    ),
                    "route_fails": int(route_fails),
                    "baseline_ff_eta": free_flow_eta,
                    "label_source": "deterministic_simulator",
                }
                row.update(features)
                rows.append(row)

    return rows


def summarise(rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Describe a dataset without claiming accuracy."""
    failures = sum(int(row["route_fails"]) for row in rows)
    return {
        "rows": len(rows),
        "scenarios": len({row["scenario_id"] for row in rows}),
        "failures": failures,
        "failure_rate": (failures / len(rows)) if rows else 0.0,
        "label_source": "deterministic_simulator",
        "accuracy_evaluated": False,
    }