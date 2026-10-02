"""Prediction request, result, and persistence response contracts."""

from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, field_validator, model_validator


class PredictionKind(str, Enum):
    ETA = "ETA"
    ROUTE_FAILURE = "ROUTE_FAILURE"
    CONGESTION = "CONGESTION"
    HAZARD_IMPACT = "HAZARD_IMPACT"


class PredictionStatus(str, Enum):
    AVAILABLE = "AVAILABLE"
    UNAVAILABLE = "UNAVAILABLE"


class PredictionSeverity(str, Enum):
    LOW = "LOW"
    MODERATE = "MODERATE"
    HIGH = "HIGH"


class PredictionRequest(BaseModel):
    prediction_type: PredictionKind
    correlation_id: UUID = Field(default_factory=uuid4)
    horizon_seconds: int = Field(default=300, ge=1, le=86_400)


class PredictionFactor(BaseModel):
    factor: str = Field(min_length=1, max_length=120)
    contribution: float = Field(ge=0, le=1, allow_inf_nan=False)
    description: str = Field(min_length=1, max_length=500)


class PredictionResult(BaseModel):
    prediction_type: PredictionKind
    status: PredictionStatus = PredictionStatus.AVAILABLE
    value: dict[str, Any] | None = None
    probability: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    confidence: float = Field(ge=0, le=1, allow_inf_nan=False)
    severity: PredictionSeverity | None = None
    horizon_seconds: int = Field(ge=1, le=86_400)
    factors: list[PredictionFactor] = Field(default_factory=list)
    model_version: str = Field(min_length=1, max_length=80)
    source: str = Field(min_length=1, max_length=120)
    reason: str | None = None
    missing_inputs: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def require_status_details(self) -> "PredictionResult":
        if not self.factors:
            raise ValueError("predictions require at least one explainability factor")
        if self.status is PredictionStatus.UNAVAILABLE:
            if not self.reason or not self.missing_inputs:
                raise ValueError("unavailable predictions require a reason and missing_inputs")
            if self.value is not None:
                raise ValueError("unavailable predictions cannot include a value")
        elif self.value is None:
            raise ValueError("available predictions require a value")
        return self


class PredictionRead(BaseModel):
    prediction_id: UUID
    mission_id: UUID
    prediction_type: PredictionKind
    timestamp: datetime
    correlation_id: UUID
    horizon_seconds: int | None
    status: PredictionStatus
    value: dict[str, Any] | None
    probability: float | None
    confidence: float | None
    severity: PredictionSeverity | None
    factors: list[PredictionFactor]
    model_version: str | None
    source: str
    reason: str | None
    missing_inputs: list[str]
    metadata: dict[str, Any]
    created_at: datetime

    @field_validator("timestamp", "created_at")
    @classmethod
    def require_aware_timestamps(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("prediction timestamps must include a timezone")
        return value.astimezone(timezone.utc)


class PredictionHistory(BaseModel):
    items: list[PredictionRead]
    limit: int
    has_more: bool

