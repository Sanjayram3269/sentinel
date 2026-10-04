"""Deterministic inference with an optional learned residual model.

The learned model is *optional by design*. No trained artifact is committed to
this repository, and the runtime that produced the original artifacts is not a
project dependency, so in this checkout every prediction resolves to the
deterministic baseline and reports ``source="baseline_fallback"``. Nothing here
invents an ML result: if the artifact is missing, unreadable, or outside the
feature range it was trained on, the baseline is returned and the reason is
recorded.

A process-wide cache keeps artifact loading to once per process rather than
once per request.
"""

from __future__ import annotations

import os
import pickle
from typing import Any, Dict, List, NamedTuple, Optional, Sequence

import networkx as nx
import numpy as np

from sentinel_ai.contracts import WorldState
from sentinel_ai.prediction.baselines import baseline_current_speed_eta
from sentinel_ai.prediction.features import compute_route_features

__all__ = [
    "FEATURE_COLS",
    "MODEL_FILENAME",
    "EtaInference",
    "RiskInference",
    "load_models",
    "reset_model_cache",
    "predict_eta",
    "predict_risk",
]

#: Feature order the original artifacts were trained against. The set is part
#: of the AI feature contract and must not drift silently: a model trained on
#: different columns cannot be fed these features.
FEATURE_COLS = [
    "length",
    "free_flow_time",
    "mean_current_speed",
    "min_current_speed",
    "max_occupancy",
    "num_signals",
    "share_arterial",
    "min_distance_incident",
    "time_of_day",
    "demand_level",
    "forecast_trend",
]

#: Filename the trainer writes inside the configured model directory.
MODEL_FILENAME = "sentinel_models.pkl"

#: ``None`` means "not loaded yet"; anything else is the resolved artifact or
#: the sentinel string ``"baseline_fallback"``.
_MODEL_CACHE: Any = None


class EtaInference(NamedTuple):
    """ETA in minutes plus the provenance fields the contract requires."""

    eta_p10_min: float
    eta_p50_min: float
    eta_p90_min: float
    baseline_eta_min: float
    source: str
    confidence: float
    reasons: List[str]
    evidence: Dict[str, Any]


class RiskInference(NamedTuple):
    """Route-failure outcome plus provenance."""

    route_fails: bool
    failure_probability: float
    source: str
    confidence: float
    reasons: List[str]
    evidence: Dict[str, Any]


def reset_model_cache() -> None:
    """Drop the cached artifact. Used by tests; not called per request."""
    global _MODEL_CACHE
    _MODEL_CACHE = None


def load_models(model_dir: Optional[str] = None) -> Any:
    """Resolve the artifact once per process.

    Returns the unpickled artifact, or ``"baseline_fallback"`` when no usable
    artifact exists. A directory that is relative is refused outright: an
    artifact resolved against the current working directory would silently
    change meaning with the process launch directory.
    """
    global _MODEL_CACHE
    if _MODEL_CACHE is not None:
        return _MODEL_CACHE

    if model_dir is None:
        model_dir = os.environ.get("AI_MODEL_PATH")
    if not model_dir or not os.path.isabs(model_dir):
        _MODEL_CACHE = "baseline_fallback"
        return _MODEL_CACHE

    model_path = os.path.join(model_dir, MODEL_FILENAME)
    if not os.path.isfile(model_path):
        _MODEL_CACHE = "baseline_fallback"
        return _MODEL_CACHE

    try:
        with open(model_path, "rb") as handle:
            _MODEL_CACHE = pickle.load(handle)
    except Exception:
        # A truncated or dependency-missing artifact must degrade to the
        # baseline rather than break the request path.
        _MODEL_CACHE = "baseline_fallback"
    return _MODEL_CACHE


def _resolve_eta(
    models: Any, features: Dict[str, float], baseline_eta: float
) -> Optional[EtaInference]:
    """Apply the learned residual model, or ``None`` to keep the baseline."""
    for column, bounds in models.get("feature_ranges", {}).items():
        if column in features:
            value = float(features[column])
            low, high = bounds
            if value < low or value > high:
                return None

    row = _np.array([[features.get(column, 0.0) for column in FEATURE_COLS]])
    p10 = float(models["eta_p10"].predict(row)[0])
    p50 = float(models["eta_p50"].predict(row)[0])
    p90 = float(models["eta_p90"].predict(row)[0])

    quantile_hat = models.get("q_hat", 0.0)
    p10 = max(0.0, baseline_eta * np.exp(p10) - quantile_hat)
    p50 = baseline_eta * np.exp(p50)
    p90 = baseline_eta * np.exp(p90) + quantile_hat

    if not models.get("use_ml_p50", True):
        # The artifact was validated as interval-only.
        return EtaInference(
            eta_p10_min=p10,
            eta_p50_min=baseline_eta,
            eta_p90_min=p90,
            baseline_eta_min=baseline_eta,
            source="baseline_fallback",
            confidence=0.9,
            reasons=["artifact is interval-only; median kept on the deterministic baseline"],
            evidence={"model_p50": p50},
        )

    return EtaInference(
        eta_p10_min=p10,
        eta_p50_min=p50,
        eta_p90_min=p90,
        baseline_eta_min=baseline_eta,
        source="ml",
        confidence=0.9,
        reasons=[],
        evidence={"model_p50": p50},
    )


def _resolve_risk(models: Any, features: Dict[str, float]) -> Optional[RiskInference]:
    for column, bounds in models.get("feature_ranges", {}).items():
        if column in features:
            value = float(features[column])
            low, high = bounds
            if value < low or value > high:
                return None

    row = np.array([[features.get(column, 0.0) for column in FEATURE_COLS]])
    probability = float(models["risk_clf"].predict_proba(row)[0, 1])
    return RiskInference(
        route_fails=probability > 0.5,
        failure_probability=probability,
        source="ml",
        confidence=0.85,
        reasons=[],
        evidence={},
    )


def _is_fallback(models: Any) -> bool:
    return models is None or models == "baseline_fallback"


def predict_eta(
    G: nx.DiGraph,
    route: Sequence[str],
    depart_time_utc: float,
    world_state: WorldState,
    model_dir: Optional[str] = None,
) -> EtaInference:
    """Predict a route ETA in minutes from real road-graph features."""
    models = load_models(model_dir)
    features = compute_route_features(G, list(route), depart_time_utc, world_state)
    baseline_eta = baseline_current_speed_eta(G, list(route), features)

    if _is_fallback(models):
        reason = (
            "no trained ETA artifact configured or loadable; "
            "returned the deterministic current-speed baseline"
        )
        return EtaInference(
            eta_p10_min=baseline_eta,
            eta_p50_min=baseline_eta,
            eta_p90_min=baseline_eta,
            baseline_eta_min=baseline_eta,
            source="baseline_fallback",
            confidence=0.0,
            reasons=[reason],
            evidence={"features": features},
        )

    try:
        resolved = _resolve_eta(models, features, baseline_eta)
    except Exception:
        resolved = None
    if resolved is None:
        return EtaInference(
            eta_p10_min=baseline_eta,
            eta_p50_min=baseline_eta,
            eta_p90_min=baseline_eta,
            baseline_eta_min=baseline_eta,
            source="baseline_fallback",
            confidence=0.0,
            reasons=[
                "trained ETA artifact rejected the feature vector; "
                "returned the deterministic current-speed baseline"
            ],
            evidence={"features": features},
        )
    return resolved._replace(evidence={**resolved.evidence, "features": features})


def predict_risk(
    G: nx.DiGraph,
    route: Sequence[str],
    depart_time_utc: float,
    world_state: WorldState,
    model_dir: Optional[str] = None,
) -> RiskInference:
    """Predict route-failure probability from real road-graph features."""
    models = load_models(model_dir)
    features = compute_route_features(G, list(route), depart_time_utc, world_state)

    def baseline() -> RiskInference:
        fails = features.get("closed_edge_flag", 0.0) > 0.5
        return RiskInference(
            route_fails=fails,
            failure_probability=1.0 if fails else 0.0,
            source="baseline_fallback",
            confidence=0.0,
            reasons=[
                "no trained route-failure artifact configured or loadable; "
                "returned the deterministic closed-edge rule"
            ],
            evidence={"features": features},
        )

    if _is_fallback(models):
        return baseline()

    try:
        resolved = _resolve_risk(models, features)
    except Exception:
        resolved = None
    if resolved is None:
        return RiskInference(
            **{**baseline()._asdict(), "reasons": [
                "trained route-failure artifact rejected the feature vector; "
                "returned the deterministic closed-edge rule"
            ]},
        )
    return resolved._replace(evidence={**resolved.evidence, "features": features})