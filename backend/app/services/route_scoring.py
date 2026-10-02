"""Configurable deterministic route scoring with explicit missing-data handling."""

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from app.config import Settings
from app.schemas.routing import RouteProposal


@dataclass(frozen=True)
class RouteScoreWeights:
    eta: float
    distance: float
    risk: float
    failure: float
    congestion: float
    hazard: float

    @classmethod
    def from_settings(cls, settings: Settings) -> "RouteScoreWeights":
        return cls(
            eta=settings.route_weight_eta,
            distance=settings.route_weight_distance,
            risk=settings.route_weight_risk,
            failure=settings.route_weight_failure,
            congestion=settings.route_weight_congestion,
            hazard=settings.route_weight_hazard,
        )


@dataclass(frozen=True)
class PredictionSignals:
    eta_seconds: float | None = None
    failure_probability: float | None = None
    congestion_score: float | None = None
    hazard_exposure: float | None = None


@dataclass(frozen=True)
class ScoredRoute:
    candidate_id: UUID
    proposal: RouteProposal
    score: float
    score_coverage: float
    viable: bool
    rejection_reasons: tuple[str, ...]
    metrics: dict[str, float]
    provider_order: int = 0


class RouteScoringEngine:
    def __init__(
        self,
        weights: RouteScoreWeights,
        *,
        failure_threshold: float,
        hazard_threshold: float,
    ) -> None:
        self.weights = weights
        self.failure_threshold = failure_threshold
        self.hazard_threshold = hazard_threshold

    def score(
        self,
        candidates: list[tuple[UUID, RouteProposal]],
        predictions: dict[UUID, PredictionSignals] | None = None,
    ) -> list[ScoredRoute]:
        predictions = predictions or {}
        eta_values = [
            predictions.get(candidate_id, PredictionSignals()).eta_seconds
            if predictions.get(candidate_id, PredictionSignals()).eta_seconds is not None
            else proposal.estimated_duration_seconds
            for candidate_id, proposal in candidates
        ]
        distance_values = [proposal.distance_meters for _, proposal in candidates]
        normalized_eta = self._normalize(eta_values)
        normalized_distance = self._normalize(distance_values)
        total_weight = sum(
            (
                self.weights.eta,
                self.weights.distance,
                self.weights.risk,
                self.weights.failure,
                self.weights.congestion,
                self.weights.hazard,
            )
        )
        scored: list[ScoredRoute] = []
        for index, (candidate_id, proposal) in enumerate(candidates):
            signal = predictions.get(candidate_id, PredictionSignals())
            values: dict[str, float | None] = {
                "eta": normalized_eta[index],
                "distance": normalized_distance[index],
                "risk": proposal.risk_score,
                "failure": (
                    proposal.predicted_failure_probability
                    if proposal.predicted_failure_probability is not None
                    else signal.failure_probability
                ),
                "congestion": (
                    proposal.congestion_score
                    if proposal.congestion_score is not None
                    else signal.congestion_score
                ),
                "hazard": (
                    proposal.hazard_exposure
                    if proposal.hazard_exposure is not None
                    else signal.hazard_exposure
                ),
            }
            weights = {
                "eta": self.weights.eta,
                "distance": self.weights.distance,
                "risk": self.weights.risk,
                "failure": self.weights.failure,
                "congestion": self.weights.congestion,
                "hazard": self.weights.hazard,
            }
            available_weight = sum(weights[key] for key, value in values.items() if value is not None)
            score = (
                sum(weights[key] * value for key, value in values.items() if value is not None)
                / available_weight
                if available_weight
                else 1.0
            )
            coverage = available_weight / total_weight if total_weight else 0.0
            failure = values["failure"]
            hazard = values["hazard"]
            reasons = []
            if failure is not None and failure > self.failure_threshold:
                reasons.append("failure_probability_exceeds_threshold")
            if hazard is not None and hazard > self.hazard_threshold:
                reasons.append("hazard_exposure_exceeds_threshold")
            if proposal.reachable is False:
                reasons.append("destination_unreachable")
            if len(proposal.geometry) < 2:
                reasons.append("route_geometry_missing")
            scored.append(
                ScoredRoute(
                    candidate_id=candidate_id,
                    proposal=proposal,
                    score=min(1.0, max(0.0, score)),
                    score_coverage=min(1.0, max(0.0, coverage)),
                    viable=not reasons,
                    rejection_reasons=tuple(reasons),
                    metrics={key: value for key, value in values.items() if value is not None},
                    provider_order=index,
                )
            )
        return scored

    @staticmethod
    def _normalize(values: list[float]) -> list[float]:
        if len(values) <= 1:
            return [0.5 for _ in values]
        low, high = min(values), max(values)
        if high == low:
            return [0.5 for _ in values]
        return [(value - low) / (high - low) for value in values]


