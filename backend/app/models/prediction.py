"""Persisted model predictions for later decision workflows."""

from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from sqlalchemy import (
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, UUIDPrimaryKey
from app.models.enums import PredictionType, enum_values

if TYPE_CHECKING:
    from app.models.mission import Mission


class Prediction(UUIDPrimaryKey, Base):
    __tablename__ = "predictions"
    __table_args__ = (Index("ix_predictions_mission_observed", "mission_id", "observed_at"),)

    mission_id: Mapped[UUID] = mapped_column(
        ForeignKey("missions.id", ondelete="CASCADE"), nullable=False
    )
    prediction_type: Mapped[PredictionType] = mapped_column(
        Enum(PredictionType, name="prediction_type", values_callable=enum_values),
        nullable=False,
    )
    vehicle_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("vehicles.id", ondelete="SET NULL")
    )
    route_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("routes.id", ondelete="SET NULL")
    )
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    correlation_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), nullable=False, default=uuid4
    )
    horizon_seconds: Mapped[int | None] = mapped_column(Integer)
    predicted_value: Mapped[Any] = mapped_column(JSONB, nullable=False)
    probability: Mapped[float | None] = mapped_column(Float)
    confidence: Mapped[float | None] = mapped_column(Float)
    factors: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    source: Mapped[str] = mapped_column(
        String(120), nullable=False, default="sentinel_prediction_engine"
    )
    prediction_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    model_name: Mapped[str | None] = mapped_column(String(160))
    model_version: Mapped[str | None] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    mission: Mapped["Mission"] = relationship(back_populates="predictions")