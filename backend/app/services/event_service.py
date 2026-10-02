"""Event persistence, mission transitions, history, and publication."""

from datetime import datetime
from uuid import UUID

from fastapi import HTTPException
from redis.exceptions import RedisError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Event, Mission
from app.models.enums import MissionStatus
from app.schemas.events import EventCreate, EventType
from app.services.event_bus import EventPublisher

MISSION_TRANSITIONS: dict[MissionStatus, set[MissionStatus]] = {
    MissionStatus.CREATED: {MissionStatus.DISPATCHED, MissionStatus.CANCELLED},
    MissionStatus.DISPATCHED: {MissionStatus.ACTIVE, MissionStatus.CANCELLED},
    MissionStatus.ACTIVE: {MissionStatus.COMPLETED, MissionStatus.CANCELLED},
    MissionStatus.PAUSED: set(),
    MissionStatus.COMPLETED: set(),
    MissionStatus.CANCELLED: set(),
}


def validate_transition(current: MissionStatus, target: MissionStatus) -> None:
    if target not in MISSION_TRANSITIONS[current]:
        raise HTTPException(
            status_code=409,
            detail=f"Mission cannot transition from {current.value} to {target.value}",
        )


class EventService:
    def __init__(self, publisher: EventPublisher) -> None:
        self.publisher = publisher

    async def create(
        self,
        db: AsyncSession,
        mission_id: UUID,
        payload: EventCreate,
    ) -> Event:
        async with db.begin():
            event = await self.persist(db, mission_id, payload)
        await self.publish_persisted(event)
        return event

    async def persist(
        self,
        db: AsyncSession,
        mission_id: UUID,
        payload: EventCreate,
    ) -> Event:
        """Persist an event inside the caller's transaction without publishing."""
        mission = await db.scalar(
            select(Mission).where(Mission.id == mission_id).with_for_update()
        )
        if mission is None:
            raise HTTPException(status_code=404, detail="Mission not found")

        if payload.event_type is EventType.MISSION_STATUS_CHANGED:
            target_value = payload.payload.get("status")
            try:
                target = MissionStatus(target_value)
            except (ValueError, TypeError):
                raise HTTPException(
                    status_code=422,
                    detail="MISSION_STATUS_CHANGED payload requires a valid status",
                ) from None
            validate_transition(mission.status, target)
            mission.status = target
            if target is MissionStatus.ACTIVE:
                mission.started_at = payload.timestamp
            elif target is MissionStatus.COMPLETED:
                mission.completed_at = payload.timestamp

        event = Event(
            mission_id=mission_id,
            event_type=payload.event_type.value,
            source=payload.source,
            correlation_id=payload.correlation_id,
            occurred_at=payload.timestamp,
            payload=payload.payload,
        )
        db.add(event)
        await db.flush()
        await db.refresh(event)
        return event

    async def publish_persisted(self, event: Event) -> None:
        """Publish a committed event using the configured event-bus adapter."""
        try:
            await self.publisher.publish(event)
        except (RedisError, OSError) as error:
            raise HTTPException(
                status_code=503,
                detail="Event was persisted but could not be published; retry delivery later",
            ) from error

    async def history(
        self,
        db: AsyncSession,
        mission_id: UUID,
        *,
        event_type: EventType | None,
        limit: int,
        before: datetime | None,
        after: datetime | None,
    ) -> tuple[list[Event], bool]:
        mission_exists = await db.scalar(select(Mission.id).where(Mission.id == mission_id))
        if mission_exists is None:
            raise HTTPException(status_code=404, detail="Mission not found")

        statement = select(Event).where(Event.mission_id == mission_id)
        if event_type is not None:
            statement = statement.where(Event.event_type == event_type.value)
        if before is not None:
            statement = statement.where(Event.occurred_at < before)
        if after is not None:
            statement = statement.where(Event.occurred_at > after)
        statement = statement.order_by(
            Event.occurred_at.desc(), Event.created_at.desc(), Event.id.desc()
        ).limit(limit + 1)
        events = list((await db.scalars(statement)).all())
        return events[:limit], len(events) > limit