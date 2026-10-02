"""Mission prediction execution and bounded prediction history endpoints."""

from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.schemas.predictions import (
    PredictionHistory,
    PredictionKind,
    PredictionRead,
    PredictionRequest,
)
from app.services.event_bus import EventPublisher
from app.services.prediction_service import PredictionService, prediction_read

router = APIRouter(prefix="/missions", tags=["predictions"])


def _prediction_service(request: Request) -> PredictionService:
    return PredictionService.with_publisher(EventPublisher(request.app.state.redis))


@router.post(
    "/{mission_id}/predictions",
    response_model=PredictionRead,
    status_code=status.HTTP_201_CREATED,
    summary="Run and persist a mission prediction",
    description=(
        "Executes a deterministic baseline predictor from persisted mission state. "
        "Baseline scores are explainable rules, not trained-model probabilities."
    ),
)
async def run_mission_prediction(
    mission_id: UUID,
    payload: PredictionRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> PredictionRead:
    return await _prediction_service(request).run_prediction(
        db,
        mission_id,
        payload.prediction_type,
        correlation_id=payload.correlation_id,
        horizon_seconds=payload.horizon_seconds,
    )


@router.get(
    "/{mission_id}/predictions",
    response_model=PredictionHistory,
    summary="List mission predictions",
)
async def get_mission_predictions(
    mission_id: UUID,
    request: Request,
    prediction_type: PredictionKind | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    before: datetime | None = None,
    after: datetime | None = None,
    db: AsyncSession = Depends(get_db),
) -> PredictionHistory:
    if before is not None and (before.tzinfo is None or before.utcoffset() is None):
        raise HTTPException(status_code=422, detail="before must include a timezone")
    if after is not None and (after.tzinfo is None or after.utcoffset() is None):
        raise HTTPException(status_code=422, detail="after must include a timezone")
    before = before.astimezone(timezone.utc) if before is not None else None
    after = after.astimezone(timezone.utc) if after is not None else None
    predictions, has_more = await _prediction_service(request).history(
        db,
        mission_id,
        prediction_type=prediction_type,
        limit=limit,
        before=before,
        after=after,
    )
    return PredictionHistory(
        items=[prediction_read(item) for item in predictions],
        limit=limit,
        has_more=has_more,
    )


@router.get(
    "/{mission_id}/predictions/{prediction_id}",
    response_model=PredictionRead,
    summary="Get a mission prediction",
)
async def get_mission_prediction(
    mission_id: UUID,
    prediction_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> PredictionRead:
    prediction = await _prediction_service(request).get(db, mission_id, prediction_id)
    return prediction_read(prediction)