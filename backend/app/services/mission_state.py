"""Deterministic mission-state aggregation from persisted domain records."""

from typing import Any
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Event,
    Incident,
    Mission,
    MissionPlan,
    Route,
    Vehicle,
    VehicleTelemetry,
)
from app.models.enums import PlanStatus, RouteStatus
from app.schemas.domain import (
    EventState,
    GeoPoint,
    IncidentRead,
    MissionRead,
    MissionStateRead,
    RouteState,
    VehicleRead,
)


async def get_mission_state(db: AsyncSession, mission_id: UUID) -> MissionStateRead:
    mission = await db.get(Mission, mission_id)
    if mission is None:
        raise HTTPException(status_code=404, detail="Mission not found")

    incident_rows = (
        await db.execute(
            select(
                Incident,
                func.ST_Y(Incident.location),
                func.ST_X(Incident.location),
            )
            .where(Incident.mission_id == mission_id)
            .order_by(Incident.occurred_at.desc(), Incident.id.desc())
        )
    ).all()
    incidents = [
        IncidentRead(
            id=row.Incident.id,
            mission_id=row.Incident.mission_id,
            type=row.Incident.type,
            severity=row.Incident.severity,
            description=row.Incident.description,
            location=GeoPoint(latitude=row[1], longitude=row[2]),
            occurred_at=row.Incident.occurred_at,
            active=row.Incident.active,
            created_at=row.Incident.created_at,
            updated_at=row.Incident.updated_at,
        )
        for row in incident_rows
    ]

    latest_telemetry = (
        select(
            VehicleTelemetry.vehicle_id.label("vehicle_id"),
            VehicleTelemetry.observed_at.label("observed_at"),
            VehicleTelemetry.position.label("position"),
            VehicleTelemetry.speed.label("speed"),
            VehicleTelemetry.heading.label("heading"),
            VehicleTelemetry.status.label("status"),
            func.row_number()
            .over(
                partition_by=VehicleTelemetry.vehicle_id,
                order_by=(
                    VehicleTelemetry.observed_at.desc(),
                    VehicleTelemetry.id.desc(),
                ),
            )
            .label("row_number"),
        )
        .subquery()
    )
    vehicle_rows = (
        await db.execute(
            select(
                Vehicle,
                func.coalesce(
                    func.ST_Y(latest_telemetry.c.position),
                    func.ST_Y(Vehicle.current_location),
                ),
                func.coalesce(
                    func.ST_X(latest_telemetry.c.position),
                    func.ST_X(Vehicle.current_location),
                ),
                func.coalesce(latest_telemetry.c.speed, Vehicle.speed),
                func.coalesce(latest_telemetry.c.heading, Vehicle.heading),
                func.coalesce(latest_telemetry.c.status, Vehicle.status),
                latest_telemetry.c.observed_at,
            )
            .outerjoin(
                latest_telemetry,
                and_(
                    latest_telemetry.c.vehicle_id == Vehicle.id,
                    latest_telemetry.c.row_number == 1,
                ),
            )
            .where(Vehicle.mission_id == mission_id)
            .order_by(Vehicle.id)
        )
    ).all()
    vehicles = [
        VehicleRead(
            id=row.Vehicle.id,
            mission_id=row.Vehicle.mission_id,
            vehicle_type=row.Vehicle.vehicle_type,
            status=row[5],
            call_sign=row.Vehicle.call_sign,
            capability=row.Vehicle.capability,
            current_location=(
                GeoPoint(latitude=row[1], longitude=row[2])
                if row[1] is not None and row[2] is not None
                else None
            ),
            latitude=row[1],
            longitude=row[2],
            heading=row[4],
            speed=row[3],
            created_at=row.Vehicle.created_at,
            updated_at=row.Vehicle.updated_at,
        )
        for row in vehicle_rows
    ]

    routes = list(
        (
            await db.scalars(
                select(Route)
                .where(
                    Route.mission_id == mission_id,
                    Route.status == RouteStatus.ACTIVE,
                )
                .order_by(Route.created_at.desc(), Route.id.desc())
            )
        ).all()
    )
    route_states = [
        RouteState(
            id=route.id,
            vehicle_id=route.vehicle_id,
            status=route.status,
            name=route.name,
            distance_meters=route.distance_meters,
            estimated_duration_seconds=route.estimated_duration_seconds,
            risk_score=route.risk_score,
        )
        for route in routes
    ]
    plan = await db.scalar(
        select(MissionPlan)
        .where(
            MissionPlan.mission_id == mission_id,
            MissionPlan.status == PlanStatus.APPROVED,
        )
        .order_by(MissionPlan.version.desc(), MissionPlan.id.desc())
        .limit(1)
    )
    events = list(
        (
            await db.scalars(
                select(Event)
                .where(Event.mission_id == mission_id)
                .order_by(
                    Event.occurred_at.desc(), Event.created_at.desc(), Event.id.desc()
                )
                .limit(20)
            )
        ).all()
    )

    timestamps = [mission.updated_at]
    timestamps.extend(item.updated_at for item in incidents)
    timestamps.extend(row.Vehicle.updated_at for row in vehicle_rows)
    timestamps.extend(row[6] for row in vehicle_rows if row[6] is not None)
    timestamps.extend(route.updated_at for route in routes)
    timestamps.extend(event.created_at for event in events)
    if plan is not None:
        timestamps.append(plan.updated_at)
    active_plan: dict[str, Any] | None = None
    if plan is not None:
        active_plan = {
            "id": plan.id,
            "version": plan.version,
            "status": plan.status,
            "objective": plan.objective,
            "plan_payload": plan.plan_payload,
            "created_at": plan.created_at,
        }

    return MissionStateRead(
        mission_id=mission.id,
        status=mission.status,
        incident=next((incident for incident in incidents if incident.active), None)
        or (incidents[0] if incidents else None),
        mission=MissionRead.model_validate(mission),
        incidents=incidents,
        vehicles=vehicles,
        active_routes=route_states,
        routes=route_states,
        active_plan=active_plan,
        latest_events=[EventState.model_validate(event) for event in events],
        updated_at=max(timestamps),
    )