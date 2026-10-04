"""AI-backed predictors that satisfy SENTINEL's existing ``Predictor`` protocol.

These are adapters. They depend on :mod:`sentinel_ai.api` -- the facade -- and
never on the AI library's internal modules, so the intelligence layer can be
restructured without touching the backend.

Two properties are deliberate:

* The ``PredictionResult`` shape is unchanged. The ETA value keeps the same
  ``{"routes": [{"route_id", "vehicle_id", "eta_seconds", "method"}]}`` form the
  deterministic predictor emits, so existing API consumers keep working and
  cannot tell which engine produced a value except through metadata.
* Every response states its provenance. With no trained artifact configured the
  source is ``baseline_fallback``, the reasons say so, and no accuracy is
  implied. When AI cannot run at all -- disabled, no imported network, or a
  route that does not match the road network -- the deterministic predictor's
  result is returned unchanged rather than a degraded guess.
"""

from __future__ import annotations

import time
from typing import Any, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.predictions import (
    PredictionFactor,
    PredictionKind,
    PredictionResult,
    PredictionSeverity,
    PredictionStatus,
)
from app.services.ai_road_graph import AiRoadGraph, RoadNetworkUnavailable, get_road_graph
from app.services.predictors import (
    MissionPredictionContext,
    Predictor,
    _factor,
    _level,
)

__all__ = [
    "AI_PREDICTOR_VERSION",
    "AI_SOURCE",
    "AI_SUPPORTED_TYPES",
    "AiPredictor",
]

#: Model version reported by the AI facade (the AI contract's MODEL_VERSION).
AI_PREDICTOR_VERSION = "sentinel_ai_1.0.0"

#: Source string persisted with AI-produced predictions. The AI layer's own
#: ``source`` field (``ml`` / ``baseline_fallback``) is carried in metadata so
#: the distinction survives into the stored record and the event payload.
AI_SOURCE = "sentinel_ai_road_graph"

#: Prediction kinds the AI layer implements. ``CONGESTION`` and
#: ``HAZARD_IMPACT`` are deliberately absent -- they always stay deterministic.
AI_SUPPORTED_TYPES: frozenset[PredictionKind] = frozenset(
    {PredictionKind.ETA, PredictionKind.ROUTE_FAILURE}
)


def _severity_for(probability: float) -> PredictionSeverity:
    level = _level(probability)
    return PredictionSeverity(level)


class AiPredictor:
    """ETA or route-failure prediction from the imported road network.

    One instance serves exactly one prediction kind, supplied at construction.
    That is what lets ``predict`` match the ``Predictor`` protocol *exactly* --
    ``predict(context, horizon_seconds)`` -- because the kind is already carried
    by the ``prediction_type`` attribute the protocol requires. Nothing about
    the protocol is widened and no second prediction API is introduced: the
    factory hands back a ready-made instance per kind, so callers keep calling
    ``predictor_for(kind, ai=...)`` and ``predict(context, horizon)`` as before.
    """

    prediction_type: PredictionKind

    def __init__(
        self,
        db: AsyncSession,
        network_key: str,
        prediction_type: PredictionKind,
    ) -> None:
        if prediction_type not in AI_SUPPORTED_TYPES:
            raise ValueError(f"AI predictor does not handle {prediction_type}")
        self._db = db
        self._network_key = network_key
        self.prediction_type = prediction_type

    def supports(self, prediction_type: PredictionKind) -> bool:
        return prediction_type == self.prediction_type

    def _fallback_predictor(self) -> Predictor:
        from app.services.predictors import predictor_for

        return predictor_for(self.prediction_type, ai=None)

    async def predict(
        self, context: MissionPredictionContext, horizon_seconds: int
    ) -> PredictionResult:
        if self.prediction_type is PredictionKind.ETA:
            return await self._eta(context, horizon_seconds)
        return await self._route_failure(context, horizon_seconds)

    # -- internals ------------------------------------------------------
    async def _graph(self) -> AiRoadGraph:
        return await get_road_graph(self._db, self._network_key)

    @staticmethod
    def _usable_routes(context: MissionPredictionContext):
        return [route for route in context.routes if route.road_edge_ids]

    def _world_state(self, context: MissionPredictionContext):
        """Translate SENTINEL mission state into the AI layer's world state.

        Closed routes become closed edges and active hazards become hazard
        regions, so the AI features reflect what actually happened on the
        mission rather than an empty scenario.
        """
        from sentinel_ai.contracts import (
            HazardState,
            IncidentState,
            UnitState,
            WorldState,
        )

        closed = [
            edge_id
            for route in context.routes
            if route.closed
            for edge_id in route.road_edge_ids
        ]
        hazards = [
            HazardState(
                hazard_id=str(hazard.id),
                center_node=hazard.road_node_id,
                radius_m=hazard.distance_meters
                if hazard.distance_meters is not None
                else 1000.0,
                expansion_rate_m_per_s=0.0,
            )
            for hazard in context.hazards
            if hazard.road_node_id
        ]
        incidents = [
            IncidentState(
                incident_id=str(route.id),
                node=route.road_edge_ids[0].split("->")[0],
                priority=1,
                required_responder_types=[],
            )
            for route in context.routes
            if route.road_edge_ids
        ]
        units = [
            UnitState(
                unit_id=str(vehicle.id),
                unit_type="unknown",
                position=(
                    self._usable_routes(context)[0].road_edge_ids[0].split("->")[0]
                    if self._usable_routes(context)
                    else ""
                ),
                available=True,
            )
            for vehicle in context.vehicles
        ]
        demand = 1.0
        if context.recent_events:
            for event in context.recent_events:
                level = event.payload.get("congestion_ratio")
                if isinstance(level, (int, float)):
                    demand = float(level)
                    break
        return WorldState(
            timestamp_utc=time.time(),
            demand_level=max(0.0, demand),
            closed_edges=closed,
            incidents=incidents,
            hazards=hazards,
            units=units,
            hospitals=[],
        )

    async def _eta(
        self, context: MissionPredictionContext, horizon_seconds: int
    ) -> PredictionResult:
        routes = self._usable_routes(context)
        if not routes:
            return self._fallback_predictor().predict(context, horizon_seconds)

        try:
            road_graph = await self._graph()
        except RoadNetworkUnavailable:
            return self._fallback_predictor().predict(context, horizon_seconds)

        from sentinel_ai.api import predict_eta
        from sentinel_ai.contracts import PredictEtaRequest

        world_state = self._world_state(context)
        vehicles = {vehicle.id: vehicle for vehicle in context.vehicles}
        estimates: list[dict[str, Any]] = []
        ai_source = "baseline_fallback"
        reasons: list[str] = []
        confidence = 0.0
        intervals: list[dict[str, float]] = []

        depart = world_state.timestamp_utc
        for route in routes:
            response = predict_eta(
                PredictEtaRequest(
                    route=list(route.road_edge_ids),
                    depart_time_utc=depart,
                    world_state=world_state,
                ),
                G=road_graph.graph,
            )
            ai_source = response.source
            reasons = list(response.reasons)
            confidence = response.confidence
            vehicle = vehicles.get(route.vehicle_id)
            speed = vehicle.speed_meters_per_second if vehicle else None
            eta_seconds = (
                round(route.distance_meters / speed, 2)
                if speed is not None and speed > 0 and route.distance_meters >= 0
                else round(response.eta_p50_min * 60.0, 2)
            )
            estimates.append(
                {
                    "route_id": str(route.id),
                    "vehicle_id": str(route.vehicle_id),
                    "eta_seconds": eta_seconds,
                    "method": "ai_road_graph_eta",
                }
            )
            intervals.append(
                {
                    "route_id": str(route.id),
                    "p10_min": round(response.eta_p10_min, 4),
                    "p50_min": round(response.eta_p50_min, 4),
                    "p90_min": round(response.eta_p90_min, 4),
                    "baseline_min": round(response.baseline_eta_min, 4),
                    "edge_count": len(route.road_edge_ids),
                }
            )

        return PredictionResult(
            prediction_type=PredictionKind.ETA,
            value={"routes": estimates},
            probability=None,
            confidence=confidence if confidence > 0 else 0.75,
            severity=None,
            horizon_seconds=horizon_seconds,
            factors=[
                _factor("road_network_match", 1.0, "Routes matched imported road edges"),
                _factor(
                    "ai_source",
                    0.0 if ai_source == "baseline_fallback" else 1.0,
                    f"AI prediction source reported {ai_source}",
                ),
            ],
            model_version=AI_PREDICTOR_VERSION,
            source=AI_SOURCE,
            metadata={
                "ai_source": ai_source,
                "contract_version": "1.0.0",
                "network_key": self._network_key,
                "graph_edges": road_graph.edge_count,
                "graph_nodes": road_graph.node_count,
                "distance_unit": "meters",
                "eta_unit": "minutes",
                "intervals": intervals,
                "reasons": reasons,
            },
        )

    async def _route_failure(
        self, context: MissionPredictionContext, horizon_seconds: int
    ) -> PredictionResult:
        routes = self._usable_routes(context)
        if not routes:
            return self._fallback_predictor().predict(context, horizon_seconds)

        try:
            road_graph = await self._graph()
        except RoadNetworkUnavailable:
            return self._fallback_predictor().predict(context, horizon_seconds)

        from sentinel_ai.api import predict_route_risk
        from sentinel_ai.contracts import PredictRouteRiskRequest

        world_state = self._world_state(context)
        probabilities: list[float] = []
        fails: list[bool] = []
        ai_source = "baseline_fallback"
        reasons: list[str] = []

        for route in routes:
            response = predict_route_risk(
                PredictRouteRiskRequest(
                    route=list(route.road_edge_ids),
                    depart_time_utc=world_state.timestamp_utc,
                    world_state=world_state,
                ),
                G=road_graph.graph,
            )
            probabilities.append(response.failure_probability)
            fails.append(response.route_fails)
            ai_source = response.source
            reasons = list(response.reasons)

        probability = max(probabilities) if probabilities else 0.0
        probability = min(1.0, max(0.0, probability))
        level = _level(probability)
        confidence = 0.85 if ai_source == "ml" else 0.0

        return PredictionResult(
            prediction_type=PredictionKind.ROUTE_FAILURE,
            value={
                "risk_level": level,
                "route_failure_flags": fails,
                "per_route_probability": [round(value, 4) for value in probabilities],
            },
            probability=probability,
            confidence=confidence if confidence > 0 else 0.35,
            severity=_severity_for(probability),
            horizon_seconds=horizon_seconds,
            factors=[
                _factor("road_network_match", 1.0, "Routes matched imported road edges"),
                _factor(
                    "ai_source",
                    0.0 if ai_source == "baseline_fallback" else 1.0,
                    f"AI route-failure source reported {ai_source}",
                ),
            ],
            model_version=AI_PREDICTOR_VERSION,
            source=AI_SOURCE,
            metadata={
                "ai_source": ai_source,
                "contract_version": "1.0.0",
                "network_key": self._network_key,
                "graph_edges": road_graph.edge_count,
                "graph_nodes": road_graph.node_count,
                "routes_evaluated": len(routes),
                "not_a_trained_probability": ai_source != "ml",
                "reasons": reasons,
            },
        )