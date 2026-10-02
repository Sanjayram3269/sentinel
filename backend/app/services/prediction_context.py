"""Deterministic prediction feature extraction from persisted mission state."""

from uuid import UUID

from geoalchemy2 import Geography
from fastapi import HTTPException
from sqlalchemy import cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Event, Hazard, Mission, Route, Vehicle, VehicleTelemetry
from app.models.enums import HazardStatus, RouteStatus
from app.services.predictors import (
    EventFeature,
    HazardFeature,
    MissionPredictionContext,
    RouteFeature,
    VehicleFeature,
)

MAX_CONTEXT_ROUTES = 200
MAX_CONTEXT_HAZARDS = 200
MAX_RECENT_EVENTS = 20


async def build_prediction_context(
    db: AsyncSession, mission_id: UUID
) -> MissionPredictionContext:
    mission = await db.get(Mission, mission_id)
    if mission is None:
        raise HTTPException(status_code=404, detail="Mission not found")

    route_rows = (
        await db.execute(
            select(
                Route.id,
                Route.vehicle_id,
                Route.distance_meters,
                Route.estimated_duration_seconds,
                Route.risk_score,
            )
            .where(
                Route.mission_id == mission_id,
                Route.status == RouteStatus.ACTIVE,
            )
            .order_by(Route.created_at.desc(), Route.id.desc())
            .limit(MAX_CONTEXT_ROUTES)
        )
    ).all()
    routes = tuple(
        RouteFeature(
            id=row.id,
            vehicle_id=row.vehicle_id,
            distance_meters=row.distance_meters,
            estimated_duration_seconds=row.estimated_duration_seconds,
            risk_score=row.risk_score,
        )
        for row in route_rows
    )

    latest_speed = (
        select(VehicleTelemetry.speed)
        .where(VehicleTelemetry.vehicle_id == Vehicle.id)
        .order_by(VehicleTelemetry.observed_at.desc(), VehicleTelemetry.id.desc())
        .limit(1)
        .correlate(Vehicle)
        .scalar_subquery()
    )
    vehicle_rows = (
        await db.execute(
            select(Vehicle.id, func.coalesce(latest_speed, Vehicle.speed).label("speed"))
            .where(Vehicle.mission_id == mission_id)
            .order_by(Vehicle.id)
            .limit(MAX_CONTEXT_ROUTES)
        )
    ).all()
    vehicles = tuple(
        VehicleFeature(id=row.id, speed_meters_per_second=row.speed)
        for row in vehicle_rows
    )
    hazard_rows = list(
        (
            await db.execute(
                select(Hazard.id, Hazard.hazard_type, Hazard.severity)
                .where(
                    Hazard.mission_id == mission_id,
                    Hazard.status == HazardStatus.ACTIVE,
                )
                .order_by(Hazard.start_time.desc(), Hazard.id.desc())
                .limit(MAX_CONTEXT_HAZARDS)
            )
        ).all()
    )
    event_rows = (
        await db.execute(
            select(Event.event_type, Event.payload)
            .where(Event.mission_id == mission_id)
            .order_by(Event.occurred_at.desc(), Event.created_at.desc(), Event.id.desc())
            .limit(MAX_RECENT_EVENTS)
        )
    ).all()
    hazard_distances: dict[UUID, float] = {}
    if hazard_rows and routes:
        hazard_ids = [row.id for row in hazard_rows]
        route_ids = [route.id for route in routes]
        route_geography = cast(Route.geometry, Geography(srid=4326))
        hazard_geography = cast(Hazard.geometry, Geography(srid=4326))
        distance = func.ST_Distance(hazard_geography, route_geography)
        rows = (
            await db.execute(
                select(Hazard.id, func.min(distance).label("distance_meters"))
                .join(
                    Route,
                    Route.mission_id == Hazard.mission_id,
                )
                .where(
                    Hazard.id.in_(hazard_ids),
                    Route.id.in_(route_ids),
                )
                .group_by(Hazard.id)
                .limit(MAX_CONTEXT_HAZARDS)
            )
        ).all()
        hazard_distances = {row.id: float(row.distance_meters) for row in rows}

    hazards = tuple(
        HazardFeature(
            id=row.id,
            hazard_type=row.hazard_type.value,
            severity=row.severity,
            distance_meters=hazard_distances.get(row.id),
        )
        for row in hazard_rows
    )
    recent_events = tuple(
        EventFeature(event_type=row.event_type, payload=row.payload)
        for row in event_rows
    )
    hazard_event_seen = any(
        event.event_type in {"HAZARD_UPDATED", "ROAD_CLOSURE", "ACCIDENT_DETECTED"}
        for event in recent_events
    )
    return MissionPredictionContext(
        mission_id=mission_id,
        mission_status=mission.status.value,
        routes=routes,
        vehicles=vehicles,
        hazards=hazards,
        recent_events=recent_events,
        active_hazard_event_seen=hazard_event_seen,
    )