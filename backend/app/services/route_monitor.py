"""Explicit route health, failure, backup, and deviation evaluation."""

from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from fastapi import HTTPException
from geoalchemy2 import Geography
from sqlalchemy import cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.models import (
    Event,
    Hazard,
    Mission,
    Route,
    RouteCandidate,
    Vehicle,
    VehicleTelemetry,
)
from app.models.enums import HazardStatus, ResilienceRole, RouteStatus
from app.schemas.events import EventCreate, EventType
from app.schemas.predictions import PredictionKind, PredictionStatus
from app.schemas.routing import RouteHealthRead
from app.services.event_service import EventService
from app.services.prediction_service import PredictionService
from app.services.route_resilience import is_route_status_viable

HAZARD_RADIUS_METERS = 5_000.0
MAX_RECENT_ROUTE_EVENTS = 100


class RouteMonitorService:
    def __init__(
        self,
        event_service: EventService,
        prediction_service: PredictionService,
        settings: Settings | None = None,
    ) -> None:
        self.event_service = event_service
        self.prediction_service = prediction_service
        self.settings = settings or get_settings()

    async def evaluate_route_health(
        self,
        db: AsyncSession,
        mission_id: UUID,
        route_id: UUID,
    ) -> RouteHealthRead:
        correlation_id = uuid4()
        emitted_events: list[Event] = []
        async with db.begin():
            mission = await db.get(Mission, mission_id)
            if mission is None:
                raise HTTPException(status_code=404, detail="Mission not found")
            route = await db.scalar(
                select(Route)
                .where(Route.id == route_id, Route.mission_id == mission_id)
                .with_for_update()
            )
            if route is None:
                raise HTTPException(status_code=404, detail="Route not found")
            candidate = await db.scalar(
                select(RouteCandidate)
                .where(RouteCandidate.route_id == route_id)
                .with_for_update()
            )
            vehicle = await db.get(Vehicle, route.vehicle_id)
            if vehicle is None:
                raise HTTPException(status_code=409, detail="Route vehicle is unavailable")

            latest_position = (
                select(VehicleTelemetry.position)
                .where(VehicleTelemetry.vehicle_id == vehicle.id)
                .order_by(VehicleTelemetry.observed_at.desc(), VehicleTelemetry.id.desc())
                .limit(1)
                .correlate(Vehicle)
                .scalar_subquery()
            )
            latest_timestamp = (
                select(VehicleTelemetry.observed_at)
                .where(VehicleTelemetry.vehicle_id == vehicle.id)
                .order_by(VehicleTelemetry.observed_at.desc(), VehicleTelemetry.id.desc())
                .limit(1)
                .correlate(Vehicle)
                .scalar_subquery()
            )
            distance_row = (
                await db.execute(
                    select(
                        func.ST_Distance(
                            cast(func.coalesce(latest_position, Vehicle.current_location), Geography(srid=4326)),
                            cast(Route.geometry, Geography(srid=4326)),
                        ),
                        latest_timestamp,
                    )
                    .join(Vehicle, Vehicle.id == Route.vehicle_id)
                    .where(Route.id == route_id)
                )
            ).one()
            deviation_meters = (
                float(distance_row[0]) if distance_row[0] is not None else None
            )
            telemetry_timestamp = distance_row[1]

            prediction_map = await self.prediction_service.latest_for_route(
                db, mission_id, route_id
            )
            failure_probability = (
                candidate.predicted_failure_probability if candidate else None
            )
            failure_prediction = prediction_map.get(PredictionKind.ROUTE_FAILURE)
            if (
                failure_probability is None
                and failure_prediction is not None
                and failure_prediction.status is PredictionStatus.AVAILABLE
            ):
                failure_probability = failure_prediction.probability

            hazard_exposure = await self._hazard_exposure(db, mission_id, route_id)
            recent_events = list(
                (
                    await db.scalars(
                        select(Event)
                        .where(
                            Event.mission_id == mission_id,
                            Event.event_type.in_(
                                [
                                    EventType.ROAD_CLOSURE.value,
                                    EventType.ACCIDENT_DETECTED.value,
                                    EventType.HAZARD_UPDATED.value,
                                    EventType.CONGESTION_CHANGED.value,
                                    EventType.ROUTE_DEVIATION.value,
                                    EventType.ROUTE_FAILED.value,
                                    EventType.BACKUP_ROUTE_DEGRADED.value,
                                ]
                            ),
                        )
                        .order_by(Event.occurred_at.desc(), Event.id.desc())
                        .limit(MAX_RECENT_ROUTE_EVENTS)
                    )
                ).all()
            )
            matching_events = [
                event
                for event in recent_events
                if event.payload.get("route_id") == str(route_id)
            ]
            closure_observed = any(
                event.event_type in {
                    EventType.ROAD_CLOSURE.value,
                    EventType.ACCIDENT_DETECTED.value,
                    EventType.HAZARD_UPDATED.value,
                }
                for event in matching_events
            )
            already_failed = route.status is RouteStatus.FAILED
            failure_detected = (
                route.status is RouteStatus.FAILED
                or closure_observed
                or (
                    failure_probability is not None
                    and failure_probability > self.settings.route_failure_threshold
                )
                or (
                    hazard_exposure is not None
                    and hazard_exposure > self.settings.route_hazard_threshold
                )
            )
            already_degraded = (
                not is_route_status_viable(route.status) and not failure_detected
            )
            deviation_detected = (
                route.status is RouteStatus.ACTIVE
                and deviation_meters is not None
                and deviation_meters > self.settings.route_deviation_threshold_meters
                and not any(
                    event.event_type == EventType.ROUTE_DEVIATION.value
                    and telemetry_timestamp is not None
                    and event.occurred_at >= telemetry_timestamp
                    for event in matching_events
                )
            )

            if failure_detected:
                route.status = RouteStatus.FAILED
                if candidate is not None:
                    candidate.status = RouteStatus.FAILED
                    candidate.backup_viable = False
                if not already_failed:
                    emitted_events.append(
                        await self._persist_event(
                            db,
                            mission_id,
                            EventType.ROUTE_FAILED,
                            correlation_id,
                            {
                                "route_id": str(route_id),
                                "vehicle_id": str(vehicle.id),
                                "failure_probability": failure_probability,
                                "hazard_exposure": hazard_exposure,
                                "reason": "ROUTE_HEALTH_THRESHOLD_CROSSED",
                            },
                        )
                    )
            elif deviation_detected:
                route.status = RouteStatus.DEGRADED
                if candidate is not None:
                    candidate.status = RouteStatus.DEGRADED
                    candidate.backup_viable = False
                emitted_events.append(
                    await self._persist_event(
                        db,
                        mission_id,
                        EventType.ROUTE_DEVIATION,
                        correlation_id,
                        {
                            "route_id": str(route_id),
                            "vehicle_id": str(vehicle.id),
                            "deviation_meters": deviation_meters,
                            "threshold_meters": self.settings.route_deviation_threshold_meters,
                        },
                    )
                )

            backup_degraded = False
            if candidate is not None:
                backup = await db.scalar(
                    select(RouteCandidate)
                    .where(
                        RouteCandidate.mission_id == mission_id,
                        RouteCandidate.vehicle_id == vehicle.id,
                        RouteCandidate.planning_cycle_id == candidate.planning_cycle_id,
                        RouteCandidate.resilience_role == ResilienceRole.BACKUP,
                        RouteCandidate.route_id != route_id,
                    )
                    .with_for_update()
                )
                if backup is not None:
                    backup_hazard = backup.hazard_exposure.get("score")
                    backup_degraded = (
                        backup.status in {RouteStatus.FAILED, RouteStatus.DEGRADED}
                        or (
                            backup.predicted_failure_probability is not None
                            and backup.predicted_failure_probability
                            > self.settings.route_failure_threshold
                        )
                        or (
                            backup_hazard is not None
                            and backup_hazard > self.settings.route_hazard_threshold
                        )
                    )
                    if backup_degraded:
                        backup.backup_viable = False
                    if backup_degraded and backup.status not in {
                        RouteStatus.DEGRADED,
                        RouteStatus.FAILED,
                    }:
                        backup.status = RouteStatus.DEGRADED
                        backup_route = await db.get(Route, backup.route_id)
                        if backup_route is not None:
                            backup_route.status = RouteStatus.DEGRADED
                        emitted_events.append(
                            await self._persist_event(
                                db,
                                mission_id,
                                EventType.BACKUP_ROUTE_DEGRADED,
                                correlation_id,
                                {
                                    "route_id": str(backup.route_id),
                                    "vehicle_id": str(vehicle.id),
                                    "primary_route_id": str(route_id),
                                },
                            )
                        )

            route_degraded = already_degraded or deviation_detected
            replan_required = failure_detected or route_degraded or backup_degraded
            if replan_required and any(
                event.event_type
                in {
                    EventType.ROUTE_FAILED.value,
                    EventType.ROUTE_DEVIATION.value,
                    EventType.BACKUP_ROUTE_DEGRADED.value,
                }
                for event in emitted_events
            ):
                emitted_events.append(
                    await self._persist_event(
                        db,
                        mission_id,
                        EventType.REPLAN_TRIGGERED,
                        correlation_id,
                        {
                            "route_id": str(route_id),
                            "vehicle_id": str(vehicle.id),
                            "reason": (
                                "ACTIVE_ROUTE_FAILED"
                                if failure_detected
                                else "BACKUP_ROUTE_DEGRADED"
                                if backup_degraded
                                else "ACTIVE_ROUTE_DEVIATED"
                            ),
                            "recommended_action": "GENERATE_NEW_CANDIDATES",
                        },
                    )
                )

        for event in emitted_events:
            await self.event_service.publish_persisted(event)
        if failure_detected:
            health = "FAILED"
        elif route_degraded or backup_degraded:
            health = "DEGRADED"
        else:
            health = "HEALTHY"
        return RouteHealthRead(
            route_id=route_id,
            mission_id=mission_id,
            vehicle_id=vehicle.id,
            health=health,
            failure_probability=failure_probability,
            deviation_meters=deviation_meters,
            failure_detected=failure_detected,
            backup_degraded=backup_degraded,
            replan_required=replan_required,
            reason=(
                "ACTIVE_ROUTE_FAILED"
                if failure_detected
                else "ROUTE_DEVIATION"
                if route_degraded
                else "BACKUP_ROUTE_DEGRADED"
                if backup_degraded
                else None
            ),
            recommended_action="GENERATE_NEW_CANDIDATES" if replan_required else None,
            emitted_event_ids=[event.id for event in emitted_events],
        )

    async def _hazard_exposure(
        self, db: AsyncSession, mission_id: UUID, route_id: UUID
    ) -> float | None:
        route_geography = cast(Route.geometry, Geography(srid=4326))
        hazard_geography = cast(Hazard.geometry, Geography(srid=4326))
        distance = func.ST_Distance(hazard_geography, route_geography)
        expression = (
            (Hazard.severity / 5.0)
            * func.greatest(0.0, 1.0 - distance / HAZARD_RADIUS_METERS)
        )
        result = await db.scalar(
            select(func.max(expression))
            .select_from(Hazard)
            .join(Route, Route.id == route_id)
            .where(
                Hazard.mission_id == mission_id,
                Hazard.status == HazardStatus.ACTIVE,
                func.ST_DWithin(
                    hazard_geography, route_geography, HAZARD_RADIUS_METERS
                ),
            )
        )
        return float(result) if result is not None else None

    async def _persist_event(
        self,
        db: AsyncSession,
        mission_id: UUID,
        event_type: EventType,
        correlation_id: UUID,
        payload: dict[str, Any],
    ) -> Event:
        return await self.event_service.persist(
            db,
            mission_id,
            EventCreate(
                event_type=event_type,
                timestamp=datetime.now(timezone.utc),
                source="sentinel_route_monitor",
                correlation_id=correlation_id,
                payload=payload,
            ),
        )