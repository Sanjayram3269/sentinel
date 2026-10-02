"""Simulation execution records."""

from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import DateTime, Enum, ForeignKey, Index, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, UUIDPrimaryKey
from app.models.enums import SimulationStatus, enum_values

if TYPE_CHECKING:
    from app.models.mission import Mission


class SimulationRun(UUIDPrimaryKey, Base):
    __tablename__ = "simulation_runs"
    __table_args__ = (Index("ix_simulation_runs_mission_id", "mission_id"),)

    mission_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("missions.id", ondelete="SET NULL")
    )
    scenario_name: Mapped[str] = mapped_column(String(160), nullable=False)
    scenario_seed: Mapped[int | None] = mapped_column(Integer)
    simulator: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[SimulationStatus] = mapped_column(
        Enum(SimulationStatus, name="simulation_status", values_callable=enum_values),
        nullable=False,
        default=SimulationStatus.PENDING,
        server_default=SimulationStatus.PENDING.value,
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    configuration: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    metrics: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    mission: Mapped["Mission | None"] = relationship(back_populates="simulation_runs")