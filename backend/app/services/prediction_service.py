"""Prediction execution, persistence, event propagation, and history."""

from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Mission, Prediction
from app.models.enums import PredictionType as ModelPredictionType
from app.schemas.events import EventCreate, EventType
from app.schemas.predictions import (
    PredictionKind,
    PredictionRead,
    PredictionResult,
    PredictionStatus,
)
from app.services.event_bus import EventPublisher
from app.services.event_service import EventService
from app.services.prediction_context import build_prediction_context
from app.services.predictors import predictor_for

MAX_PREDICTION_HISTORY_LIMIT = 200


def prediction_read(record: Prediction) -> PredictionRead:
    details: dict[str, Any] = record.predicted_value
    return PredictionRead(
        prediction_id=record.id,
        mission_id=record.mission_id,
        prediction_type=PredictionKind(record.prediction_type.value),
        timestamp=record.observed_at,
        correlation_id=record.correlation_id,
        horizon_seconds=record.horizon_seconds,
        status=PredictionStatus(details["status"]),
        value=details.get("value"),
        probability=record.probability,
        confidence=record.confidence,
        severity=details.get("severity"),
        factors=record.factors,
        model_version=record.model_version,
        source=record.source,
        reason=details.get("reason"),
        missing_inputs=details.get("missing_inputs", []),
        metadata=record.prediction_metadata,
        created_at=record.created_at,
    )


class PredictionService:
    def __init__(self, event_service: EventService) -> None:
        self.event_service = event_service

    @classmethod
    def with_publisher(cls, publisher: EventPublisher) -> "PredictionService":
        return cls(EventService(publisher))

    async def run_prediction(
        self,
        db: AsyncSession,
        mission_id: UUID,
        prediction_type: PredictionKind,
        *,
        correlation_id: UUID | None = None,
        horizon_seconds: int = 300,
    ) -> PredictionRead:
        context = await build_prediction_context(db, mission_id)
        result = predictor_for(prediction_type).predict(context, horizon_seconds)
        record = self._record(mission_id, correlation_id or uuid4(), result)
        event_payload = EventCreate(
            event_type=EventType.PREDICTION_UPDATED,
            timestamp=datetime.now(timezone.utc),
            source=result.source,
            correlation_id=record.correlation_id,
            payload={
                "prediction_id": str(record.id),
                "prediction_type": result.prediction_type.value,
                "correlation_id": str(record.correlation_id),
            },
        )
        await db.commit()
        async with db.begin():
            db.add(record)
            await db.flush()
            await db.refresh(record)
            event_payload.payload["prediction_id"] = str(record.id)
            event = await self.event_service.persist(db, mission_id, event_payload)
        await self.event_service.publish_persisted(event)
        return prediction_read(record)

    async def run_all_predictions(
        self,
        db: AsyncSession,
        mission_id: UUID,
        *,
        correlation_id: UUID | None = None,
        horizon_seconds: int = 300,
    ) -> list[PredictionRead]:
        shared_correlation_id = correlation_id or uuid4()
        results = []
        for prediction_type in PredictionKind:
            results.append(
                await self.run_prediction(
                    db,
                    mission_id,
                    prediction_type,
                    correlation_id=shared_correlation_id,
                    horizon_seconds=horizon_seconds,
                )
            )
        return results

    async def history(
        self,
        db: AsyncSession,
        mission_id: UUID,
        *,
        prediction_type: PredictionKind | None,
        limit: int,
        before: datetime | None,
        after: datetime | None,
    ) -> tuple[list[Prediction], bool]:
        exists = await db.scalar(select(Mission.id).where(Mission.id == mission_id))
        if exists is None:
            raise HTTPException(status_code=404, detail="Mission not found")
        limit = min(max(limit, 1), MAX_PREDICTION_HISTORY_LIMIT)
        statement = select(Prediction).where(Prediction.mission_id == mission_id)
        if prediction_type is not None:
            statement = statement.where(
                Prediction.prediction_type == ModelPredictionType(prediction_type.value)
            )
        if before is not None:
            statement = statement.where(Prediction.observed_at < before)
        if after is not None:
            statement = statement.where(Prediction.observed_at > after)
        statement = statement.order_by(
            Prediction.observed_at.desc(), Prediction.created_at.desc(), Prediction.id.desc()
        ).limit(limit + 1)
        predictions = list((await db.scalars(statement)).all())
        return predictions[:limit], len(predictions) > limit

    async def latest_by_type(
        self, db: AsyncSession, mission_id: UUID
    ) -> dict[PredictionKind, PredictionRead]:
        """Return the latest persisted prediction for each supported type."""
        exists = await db.scalar(select(Mission.id).where(Mission.id == mission_id))
        if exists is None:
            raise HTTPException(status_code=404, detail="Mission not found")
        statement = (
            select(Prediction)
            .where(Prediction.mission_id == mission_id)
            .order_by(
                Prediction.observed_at.desc(),
                Prediction.created_at.desc(),
                Prediction.id.desc(),
            )
            .limit(100)
        )
        predictions = (await db.scalars(statement)).all()
        latest: dict[PredictionKind, PredictionRead] = {}
        for prediction in predictions:
            try:
                prediction_type = PredictionKind(prediction.prediction_type.value)
            except ValueError:
                continue
            if prediction_type not in latest:
                latest[prediction_type] = prediction_read(prediction)
        return latest

    async def latest_for_route(
        self, db: AsyncSession, mission_id: UUID, route_id: UUID
    ) -> dict[PredictionKind, PredictionRead]:
        """Return latest predictions explicitly scoped to one persisted route."""
        exists = await db.scalar(select(Mission.id).where(Mission.id == mission_id))
        if exists is None:
            raise HTTPException(status_code=404, detail="Mission not found")
        statement = (
            select(Prediction)
            .where(
                Prediction.mission_id == mission_id,
                Prediction.route_id == route_id,
            )
            .order_by(
                Prediction.observed_at.desc(),
                Prediction.created_at.desc(),
                Prediction.id.desc(),
            )
            .limit(100)
        )
        predictions = (await db.scalars(statement)).all()
        latest: dict[PredictionKind, PredictionRead] = {}
        for prediction in predictions:
            try:
                prediction_type = PredictionKind(prediction.prediction_type.value)
            except ValueError:
                continue
            if prediction_type not in latest:
                latest[prediction_type] = prediction_read(prediction)
        return latest

    async def get(
        self, db: AsyncSession, mission_id: UUID, prediction_id: UUID
    ) -> Prediction:
        mission_exists = await db.scalar(select(Mission.id).where(Mission.id == mission_id))
        if mission_exists is None:
            raise HTTPException(status_code=404, detail="Mission not found")
        prediction = await db.scalar(
            select(Prediction).where(
                Prediction.id == prediction_id,
                Prediction.mission_id == mission_id,
            )
        )
        if prediction is None:
            raise HTTPException(status_code=404, detail="Prediction not found")
        return prediction

    @staticmethod
    def _record(
        mission_id: UUID, correlation_id: UUID, result: PredictionResult
    ) -> Prediction:
        details = {
            "status": result.status.value,
            "value": result.value,
            "severity": result.severity,
            "reason": result.reason,
            "missing_inputs": result.missing_inputs,
        }
        return Prediction(
            mission_id=mission_id,
            prediction_type=ModelPredictionType(result.prediction_type.value),
            correlation_id=correlation_id,
            horizon_seconds=result.horizon_seconds,
            predicted_value=details,
            probability=result.probability,
            confidence=result.confidence,
            factors=[factor.model_dump(mode="json") for factor in result.factors],
            model_name=result.model_version,
            model_version=result.model_version,
            source=result.source,
            prediction_metadata=result.metadata,
        )