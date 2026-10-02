"""Mission event ingestion and bounded history endpoints."""

from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.schemas.events import EventCreate, EventHistory, EventRead, EventType
from app.services.event_bus import EventPublisher
from app.services.event_service import EventService

router = APIRouter(prefix="/missions", tags=["events"])


def _event_service(request: Request) -> EventService:
    return EventService(EventPublisher(request.app.state.redis))


@router.post(
    "/{mission_id}/events",
    response_model=EventRead,
    status_code=201,
    summary="Record a mission event",
)
async def create_mission_event(
    mission_id: UUID,
    payload: EventCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> EventRead:
    event = await _event_service(request).create(db, mission_id, payload)
    return EventRead.model_validate(event)


@router.get(
    "/{mission_id}/events",
    response_model=EventHistory,
    summary="List mission event history",
)
async def get_mission_events(
    mission_id: UUID,
    request: Request,
    event_type: EventType | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    before: datetime | None = None,
    after: datetime | None = None,
    db: AsyncSession = Depends(get_db),
) -> EventHistory:
    if before is not None and (before.tzinfo is None or before.utcoffset() is None):
        raise HTTPException(status_code=422, detail="before must include a timezone")
    if after is not None and (after.tzinfo is None or after.utcoffset() is None):
        raise HTTPException(status_code=422, detail="after must include a timezone")
    before = before.astimezone(timezone.utc) if before is not None else None
    after = after.astimezone(timezone.utc) if after is not None else None
    events, has_more = await _event_service(request).history(
        db,
        mission_id,
        event_type=event_type,
        limit=limit,
        before=before,
        after=after,
    )
    return EventHistory(
        items=[EventRead.model_validate(event) for event in events],
        limit=limit,
        has_more=has_more,
    )