"""The integration facade between SENTINEL and the AI layer.

Only the two prediction capabilities SENTINEL integrates in this phase are
exposed. The original ``abhy_ai`` facade also advertised route generation,
resilience scoring, hospital ranking, mission optimisation and counterfactual
simulation; those are later phases, and importing them here would drag in
``routing``, ``hospital``, ``optimization`` and ``whatif`` packages that the
backend has no use for.

The backend imports this module and never ``sentinel_ai.prediction`` or
``sentinel_ai.world`` directly, so the internal layout stays free to change.

Every response carries ``contract_version``, ``model_version``, ``source``,
``confidence``, ``reasons`` and ``evidence``. ``source`` is restricted to the
contract's ``ml`` / ``baseline_fallback`` literals: when no trained artifact is
configured the facade reports ``baseline_fallback`` and says why, rather than
implying a learned result.
"""

from __future__ import annotations

from typing import Any, Optional

import networkx as nx

from sentinel_ai.contracts import (
    CONTRACT_VERSION,
    MODEL_VERSION,
    PredictEtaRequest,
    PredictEtaResponse,
    PredictRouteRiskRequest,
    PredictRouteRiskResponse,
)
from sentinel_ai.prediction.inference import (
    predict_eta as _predict_eta,
    predict_risk as _predict_risk,
)

__all__ = [
    "CONTRACT_VERSION",
    "MODEL_VERSION",
    "predict_eta",
    "predict_route_risk",
    "ai_capabilities",
]


def ai_capabilities() -> dict[str, str]:
    """Report which prediction types the AI layer actually covers.

    ``CONGESTION`` and ``HAZARD_IMPACT`` are intentionally absent: the AI layer
    has no implementation for them, and reporting a capability that does not
    exist would let a caller assume one. SENTINEL keeps its deterministic
    predictors for those types.
    """
    return {
        "ETA": "road-graph-backed AI prediction",
        "ROUTE_FAILURE": "road-graph-backed AI prediction",
    }


def _require_graph(G: Optional[nx.DiGraph]) -> nx.DiGraph:
    """Require a caller-supplied graph.

    The original facade fell back to a synthetic 10x10 grid when no graph was
    passed. That silently produced plausible numbers for the wrong city, so a
    missing graph is now an error rather than a substitution.
    """
    if G is None:
        raise ValueError(
            "a road graph is required; build one from imported road edges via "
            "sentinel_ai.world.city_graph.build_graph_from_road_edges"
        )
    return G


def predict_eta(
    req: PredictEtaRequest, *, G: nx.DiGraph, model_dir: Optional[str] = None
) -> PredictEtaResponse:
    """Predict a route ETA in minutes."""
    graph = _require_graph(G)
    result = _predict_eta(graph, req.route, req.depart_time_utc, req.world_state, model_dir)
    return PredictEtaResponse(
        contract_version=CONTRACT_VERSION,
        model_version=MODEL_VERSION,
        source=result.source,
        confidence=result.confidence,
        eta_p10_min=result.eta_p10_min,
        eta_p50_min=result.eta_p50_min,
        eta_p90_min=result.eta_p90_min,
        baseline_eta_min=result.baseline_eta_min,
        reasons=list(result.reasons),
        evidence=dict(result.evidence),
    )


def predict_route_risk(
    req: PredictRouteRiskRequest,
    *,
    G: nx.DiGraph,
    model_dir: Optional[str] = None,
) -> PredictRouteRiskResponse:
    """Predict whether a route fails and with what probability."""
    graph = _require_graph(G)
    result = _predict_risk(graph, req.route, req.depart_time_utc, req.world_state, model_dir)
    return PredictRouteRiskResponse(
        contract_version=CONTRACT_VERSION,
        model_version=MODEL_VERSION,
        source=result.source,
        confidence=result.confidence,
        route_fails=result.route_fails,
        failure_probability=result.failure_probability,
        reasons=list(result.reasons),
        evidence=dict(result.evidence),
    )