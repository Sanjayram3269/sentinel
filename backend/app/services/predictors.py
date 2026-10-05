"""Deterministic, explainable baseline prediction implementations."""

from dataclasses import dataclass
from statistics import median
from typing import Protocol, runtime_checkable
from uuid import UUID

from app.schemas.predictions import (
    PredictionFactor,
    PredictionKind,
    PredictionResult,
    PredictionStatus,
)

BASELINE_VERSION = "baseline_rule_v1"
PREDICTION_SOURCE = "sentinel_prediction_engine"
HAZARD_IMPACT_RADIUS_METERS = 5_000.0


@dataclass(frozen=True)
class RouteFeature:
    id: UUID
    vehicle_id: UUID
    distance_meters: float
    estimated_duration_seconds: int
    risk_score: float | None
    # Real road-graph edges, ordered along the route, resolved from the route
    # geometry against the imported network. Empty when the route could not be
    # matched; the deterministic predictors ignore these and the AI adapter
    # treats them as "cannot reason on the road graph".
    road_edge_ids: tuple[str, ...] = ()
    closed: bool = False


@dataclass(frozen=True)
class VehicleFeature:
    id: UUID
    speed_meters_per_second: float | None


@dataclass(frozen=True)
class HazardFeature:
    id: UUID
    hazard_type: str
    severity: int
    distance_meters: float | None
    # Nearest road-graph node, used by the AI adapter to place the hazard on the
    # real network. ``None`` when no road network is imported.
    road_node_id: str | None = None


@dataclass(frozen=True)
class EventFeature:
    event_type: str
    payload: dict[str, object]


@dataclass(frozen=True)
class MissionPredictionContext:
    mission_id: UUID
    mission_status: str
    routes: tuple[RouteFeature, ...]
    vehicles: tuple[VehicleFeature, ...]
    hazards: tuple[HazardFeature, ...]
    recent_events: tuple[EventFeature, ...]
    active_hazard_event_seen: bool = False


@runtime_checkable
class Predictor(Protocol):
    """The one prediction entry point every engine must provide.

    ``runtime_checkable`` lets tests assert that a concrete predictor -- the
    deterministic ones below or the AI adapter -- satisfies this protocol,
    including the exact ``predict(context, horizon_seconds)`` signature. The
    protocol itself is unchanged.
    """

    prediction_type: PredictionKind

    def predict(
        self, context: MissionPredictionContext, horizon_seconds: int
    ) -> PredictionResult: ...


def _factor(name: str, contribution: float, description: str) -> PredictionFactor:
    return PredictionFactor(
        factor=name,
        contribution=max(0.0, min(1.0, contribution)),
        description=description,
    )


def _unavailable(
    prediction_type: PredictionKind,
    horizon_seconds: int,
    reason: str,
    missing_inputs: list[str],
    *,
    confidence: float = 0.0,
    factors: list[PredictionFactor] | None = None,
) -> PredictionResult:
    return PredictionResult(
        prediction_type=prediction_type,
        status=PredictionStatus.UNAVAILABLE,
        value=None,
        probability=None,
        confidence=confidence,
        severity=None,
        horizon_seconds=horizon_seconds,
        factors=factors
        or [_factor("missing_inputs", 0.0, reason)],
        model_version=BASELINE_VERSION,
        source=PREDICTION_SOURCE,
        reason=reason,
        missing_inputs=missing_inputs,
    )


def _level(probability: float) -> str:
    if probability < 0.25:
        return "LOW"
    if probability < 0.65:
        return "MODERATE"
    return "HIGH"


class EtaPredictor:
    prediction_type = PredictionKind.ETA

    def predict(
        self, context: MissionPredictionContext, horizon_seconds: int
    ) -> PredictionResult:
        if not context.routes:
            return _unavailable(
                self.prediction_type,
                horizon_seconds,
                "No active route is available for an ETA estimate",
                ["active_route"],
            )

        vehicles = {vehicle.id: vehicle for vehicle in context.vehicles}
        estimates: list[dict[str, object]] = []
        methods: set[str] = set()
        for route in sorted(context.routes, key=lambda item: item.id):
            vehicle = vehicles.get(route.vehicle_id)
            speed = vehicle.speed_meters_per_second if vehicle else None
            if speed is not None and speed > 0 and route.distance_meters >= 0:
                eta_seconds = round(route.distance_meters / speed, 2)
                method = "distance_over_vehicle_speed"
            elif route.estimated_duration_seconds > 0:
                eta_seconds = float(route.estimated_duration_seconds)
                method = "route_estimated_duration_fallback"
            else:
                continue
            methods.add(method)
            estimates.append(
                {
                    "route_id": str(route.id),
                    "vehicle_id": str(route.vehicle_id),
                    "eta_seconds": eta_seconds,
                    "method": method,
                }
            )

        if not estimates:
            return _unavailable(
                self.prediction_type,
                horizon_seconds,
                "Active routes have neither usable vehicle speed nor estimated duration",
                ["vehicle_speed", "route_estimated_duration"],
                factors=[
                    _factor("active_route", 1.0, "Active route records were available")
                ],
            )

        used_speed = "distance_over_vehicle_speed" in methods
        used_fallback = "route_estimated_duration_fallback" in methods
        factors = [_factor("active_route", 1.0, "ETA values are reported per active route")]
        if used_speed:
            factors.extend(
                [
                    _factor("route_distance", 1.0, "Route distance is measured in meters"),
                    _factor(
                        "vehicle_speed",
                        1.0,
                        "Current vehicle speed is interpreted as meters per second",
                    ),
                ]
            )
        if used_fallback:
            factors.append(
                _factor(
                    "route_estimated_duration",
                    1.0,
                    "Route duration is used where usable speed is unavailable",
                )
            )
        confidence = 0.9 if used_speed and not used_fallback else 0.75
        return PredictionResult(
            prediction_type=self.prediction_type,
            value={"routes": estimates},
            probability=None,
            confidence=confidence,
            severity=None,
            horizon_seconds=horizon_seconds,
            factors=factors,
            model_version=BASELINE_VERSION,
            source=PREDICTION_SOURCE,
            metadata={"speed_unit": "meters_per_second"},
        )


class RouteFailurePredictor:
    prediction_type = PredictionKind.ROUTE_FAILURE

    def predict(
        self, context: MissionPredictionContext, horizon_seconds: int
    ) -> PredictionResult:
        probability = 0.0
        factors: list[PredictionFactor] = []
        evidence_count = 0
        if not context.routes:
            probability += 0.45
            evidence_count += 1
            factors.append(
                _factor(
                    "active_route_unavailable",
                    0.45,
                    "No active route is available for the mission",
                )
            )
        else:
            known_risks = [
                min(1.0, max(0.0, route.risk_score))
                for route in context.routes
                if route.risk_score is not None
            ]
            if known_risks:
                risk_contribution = max(known_risks) * 0.4
                probability += risk_contribution
                evidence_count += 1
                if risk_contribution > 0:
                    factors.append(
                        _factor(
                            "route_risk_score",
                            risk_contribution,
                            "Highest active-route risk score contributes to the baseline",
                        )
                    )

        event_types = {event.event_type for event in context.recent_events}
        if "ROUTE_DEVIATION" in event_types:
            probability += 0.15
            evidence_count += 1
            factors.append(
                _factor(
                    "route_deviation",
                    0.15,
                    "A recent route-deviation event was recorded",
                )
            )
        if "ROAD_CLOSURE" in event_types:
            probability += 0.2
            evidence_count += 1
            factors.append(
                _factor("road_closure", 0.2, "A recent road-closure event was recorded")
            )
        if any(
            event.event_type == "CONGESTION_CHANGED"
            and str(event.payload.get("level", "")).upper() in {"HIGH", "SEVERE"}
            for event in context.recent_events
        ):
            probability += 0.15
            evidence_count += 1
            factors.append(
                _factor("severe_congestion", 0.15, "A recent severe congestion event was recorded")
            )
        nearby_hazards = [
            hazard
            for hazard in context.hazards
            if hazard.distance_meters is not None and hazard.distance_meters <= 1_000
        ]
        if nearby_hazards:
            contribution = max(hazard.severity for hazard in nearby_hazards) / 5 * 0.2
            probability += contribution
            evidence_count += 1
            factors.append(
                _factor(
                    "nearby_hazard",
                    contribution,
                    "An active hazard is within one kilometer of an active route",
                )
            )

        if not factors:
            factors.append(
                _factor(
                    "observable_signal_coverage",
                    0.0,
                    "No route-failure risk signals were observed in available records",
                )
            )

        probability = min(1.0, max(0.0, probability))
        confidence = min(0.95, 0.35 + evidence_count * 0.12)
        return PredictionResult(
            prediction_type=self.prediction_type,
            value={"risk_level": _level(probability)},
            probability=probability,
            confidence=confidence,
            severity=_level(probability),
            horizon_seconds=horizon_seconds,
            factors=factors,
            model_version=BASELINE_VERSION,
            source=PREDICTION_SOURCE,
            metadata={
                "method": "deterministic_weighted_rules",
                "not_a_trained_probability": True,
            },
        )


class CongestionPredictor:
    prediction_type = PredictionKind.CONGESTION

    def predict(
        self, context: MissionPredictionContext, horizon_seconds: int
    ) -> PredictionResult:
        explicit_levels = [
            str(event.payload.get("level", "")).upper()
            for event in context.recent_events
            if event.event_type == "CONGESTION_CHANGED"
            and str(event.payload.get("level", "")).upper()
            in {"LOW", "MODERATE", "MEDIUM", "HIGH", "SEVERE"}
        ]
        if explicit_levels:
            level = explicit_levels[0]
            normalized = "MODERATE" if level == "MEDIUM" else "HIGH" if level == "SEVERE" else level
            probability = {"LOW": 0.1, "MODERATE": 0.5, "HIGH": 0.85}[normalized]
            return PredictionResult(
                prediction_type=self.prediction_type,
                value={"level": normalized},
                probability=probability,
                confidence=0.8,
                severity=normalized,
                horizon_seconds=horizon_seconds,
                factors=[
                    _factor(
                        "congestion_event",
                        probability,
                        "The most recent congestion event reports this level",
                    )
                ],
                model_version=BASELINE_VERSION,
                source=PREDICTION_SOURCE,
                metadata={"method": "observed_congestion_event"},
            )

        vehicles = {vehicle.id: vehicle for vehicle in context.vehicles}
        speed_ratios: list[float] = []
        for route in context.routes:
            vehicle = vehicles.get(route.vehicle_id)
            if (
                vehicle is None
                or vehicle.speed_meters_per_second is None
                or route.distance_meters <= 0
                or route.estimated_duration_seconds <= 0
            ):
                continue
            expected_speed = route.distance_meters / route.estimated_duration_seconds
            speed_ratios.append(
                min(1.5, max(0.0, vehicle.speed_meters_per_second / expected_speed))
            )

        if not speed_ratios:
            return _unavailable(
                self.prediction_type,
                horizon_seconds,
                "No congestion event or route-relative vehicle speed is available",
                ["congestion_event", "vehicle_speed", "route_duration"],
            )

        relative_speed = median(speed_ratios)
        probability = min(1.0, max(0.0, 1.0 - min(1.0, relative_speed)))
        level = "HIGH" if probability >= 0.65 else "MODERATE" if probability >= 0.3 else "LOW"
        return PredictionResult(
            prediction_type=self.prediction_type,
            value={"level": level, "relative_speed": round(relative_speed, 4)},
            probability=probability,
            confidence=min(0.95, 0.7 + 0.05 * len(speed_ratios)),
            severity=level,
            horizon_seconds=horizon_seconds,
            factors=[
                _factor(
                    "route_relative_speed",
                    probability,
                    "Current vehicle speed is compared with route distance divided by estimated duration",
                )
            ],
            model_version=BASELINE_VERSION,
            source=PREDICTION_SOURCE,
            metadata={
                "method": "median_route_relative_speed",
                "speed_unit": "meters_per_second",
            },
        )


class HazardImpactPredictor:
    prediction_type = PredictionKind.HAZARD_IMPACT

    def predict(
        self, context: MissionPredictionContext, horizon_seconds: int
    ) -> PredictionResult:
        if not context.hazards:
            if context.active_hazard_event_seen:
                return _unavailable(
                    self.prediction_type,
                    horizon_seconds,
                    "A recent hazard event has no persisted geometry to assess",
                    ["hazard_geometry"],
                )
            return self._no_impact(horizon_seconds, "No active mission hazards were found")

        if not context.routes:
            return _unavailable(
                self.prediction_type,
                horizon_seconds,
                "Active hazards exist but no active route geometry is available",
                ["active_route_geometry"],
            )

        if any(hazard.distance_meters is None for hazard in context.hazards):
            return _unavailable(
                self.prediction_type,
                horizon_seconds,
                "Route-to-hazard distance is unavailable for one or more active hazards",
                ["hazard_route_distance"],
            )

        relevant = [
            hazard
            for hazard in context.hazards
            if hazard.distance_meters is not None
            and hazard.distance_meters <= HAZARD_IMPACT_RADIUS_METERS
        ]
        if not relevant:
            return self._no_impact(
                horizon_seconds,
                "No active hazard is within five kilometers of an active route",
                factors=[
                    _factor(
                        "hazards_outside_route_radius",
                        0.0,
                        "Active hazard geometries were compared with active route geometries",
                    )
                ],
                confidence=0.85,
            )

        risks = [
            (hazard.severity / 5)
            * max(0.0, 1 - (hazard.distance_meters or 0.0) / HAZARD_IMPACT_RADIUS_METERS)
            for hazard in relevant
        ]
        probability = min(1.0, max(risks))
        level = _level(probability)
        return PredictionResult(
            prediction_type=self.prediction_type,
            value={
                "impact_level": level,
                "relevant_hazards": [str(hazard.id) for hazard in relevant],
                "nearest_distance_meters": round(
                    min(hazard.distance_meters or 0.0 for hazard in relevant), 2
                ),
            },
            probability=probability,
            confidence=0.85,
            severity=level,
            horizon_seconds=horizon_seconds,
            factors=[
                _factor(
                    "hazard_severity_and_proximity",
                    probability,
                    "Impact score combines persisted hazard severity and PostGIS route distance",
                )
            ],
            model_version=BASELINE_VERSION,
            source=PREDICTION_SOURCE,
            metadata={
                "method": "severity_weighted_linear_proximity",
                "impact_radius_meters": HAZARD_IMPACT_RADIUS_METERS,
                "not_a_trained_probability": True,
            },
        )

    def _no_impact(
        self,
        horizon_seconds: int,
        reason: str,
        *,
        factors: list[PredictionFactor] | None = None,
        confidence: float = 0.7,
    ) -> PredictionResult:
        return PredictionResult(
            prediction_type=self.prediction_type,
            value={"impact_level": "LOW", "relevant_hazards": []},
            probability=0.0,
            confidence=confidence,
            severity="LOW",
            horizon_seconds=horizon_seconds,
            factors=factors
            or [_factor("no_active_hazards", 0.0, reason)],
            model_version=BASELINE_VERSION,
            source=PREDICTION_SOURCE,
            metadata={
                "method": "observable_hazard_scan",
                "interpretation": "No-impact baseline, not a trained probability",
            },
        )


def predictor_for(
    prediction_type: PredictionKind, ai: Predictor | None = None
) -> Predictor:
    """Select the predictor for one prediction type.

    ``ai`` is consulted first and is used only for the types it declares
    support. ``CONGESTION`` and ``HAZARD_IMPACT`` deliberately have no AI
    implementation, so those always resolve to the deterministic predictors
    below. The adapter itself falls back to the same deterministic
    implementations when AI cannot run, so a caller never has to know which
    engine produced a result.
    """
    if ai is not None and getattr(ai, "supports", None) and ai.supports(prediction_type):
        return ai
    predictors: dict[PredictionKind, Predictor] = {
        PredictionKind.ETA: EtaPredictor(),
        PredictionKind.ROUTE_FAILURE: RouteFailurePredictor(),
        PredictionKind.CONGESTION: CongestionPredictor(),
        PredictionKind.HAZARD_IMPACT: HazardImpactPredictor(),
    }
    return predictors[prediction_type]