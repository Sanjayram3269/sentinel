"""CLEARPATH simulation-evidence contracts (Phase 7).

An *evidence object* is the product of Phase 7: a measured comparison between
two simulations of the **same** mission, vehicle, route, network, demand, seed
and horizon, differing only in whether CLEARPATH pre-emption was applied.

Two properties are deliberate and load-bearing:

* Evidence never carries a decision. The Phase 6 plan is not modified, scored,
  re-versioned or superseded by a simulation. A plan that simulated badly is
  reported, not rewritten -- replanning belongs to Phase 8.
* Every derived number is ``None`` when it could not be measured from both
  runs. Nothing is estimated, and nothing is filled in from a prior run.

CLEARPATH in this system is SUMO/TraCI simulation only. No field here
authorises, requests, or reports real-world traffic-signal control.
"""

from datetime import datetime
from enum import Enum
from uuid import UUID

from pydantic import BaseModel, Field

from app.models.enums import SimulationStatus
from app.schemas.simulation import SimulationMetrics, SimulationMode


class PlanSimulationCode(str, Enum):
    """Machine-readable validation failures, never collapsed into a 500."""

    MISSION_NOT_FOUND = "MISSION_NOT_FOUND"
    PLAN_NOT_FOUND = "PLAN_NOT_FOUND"
    PLAN_MISSION_MISMATCH = "PLAN_MISSION_MISMATCH"
    PLAN_HAS_NO_SELECTED_ROUTE = "PLAN_HAS_NO_SELECTED_ROUTE"
    ROUTE_NOT_FOUND = "ROUTE_NOT_FOUND"
    ROUTE_NOT_ACTIVE = "ROUTE_NOT_ACTIVE"
    ROUTE_VEHICLE_MISMATCH = "ROUTE_VEHICLE_MISMATCH"
    INVALID_ROUTE = "INVALID_ROUTE"
    ROUTE_NETWORK_MISMATCH = "ROUTE_NETWORK_MISMATCH"
    MISSING_SUMO_EDGE_MAPPING = "MISSING_SUMO_EDGE_MAPPING"
    SIMULATION_CONFIGURATION_ERROR = "SIMULATION_CONFIGURATION_ERROR"
    SUMO_UNAVAILABLE = "SUMO_UNAVAILABLE"


class CorridorStatus(str, Enum):
    """Whether a CLEARPATH-capable corridor exists for the selected route."""

    ELIGIBLE = "ELIGIBLE_CORRIDOR"
    NO_ELIGIBLE = "NO_ELIGIBLE_CORRIDOR"


class EvidenceStatus(str, Enum):
    """Overall outcome of the evidence-producing pair."""

    COMPARABLE = "COMPARABLE"
    BASELINE_FAILED = "BASELINE_FAILED"
    CLEARPATH_FAILED = "CLEARPATH_FAILED"
    COMPARISON_UNAVAILABLE = "COMPARISON_UNAVAILABLE"


class SafetyStatus(str, Enum):
    """Simulation-only safety summary for the pair."""

    APPROVED_SIMULATION_ONLY = "APPROVED_SIMULATION_ONLY"
    NO_ACTION_PROPOSED = "NO_ACTION_PROPOSED"
    REJECTED_SIMULATION_ONLY = "REJECTED_SIMULATION_ONLY"


class PlanSimulateRequest(BaseModel):
    """Caller-controlled inputs for a plan validation run.

    Deliberately small. Route, corridor, edge mapping, vehicle and network are
    *derived* from the selected mission plan and cannot be overridden here --
    accepting a caller-supplied route would let validation measure a corridor
    the optimizer never chose.
    """

    seed: int = Field(
        default=42,
        ge=0,
        le=2_147_483_647,
        description="Same seed and config must reproduce the same measurement.",
    )
    max_simulation_seconds: int = Field(default=1800, ge=1, le=86_400)
    correlation_id: UUID | None = None
    emergency_vehicle_configuration: dict = Field(default_factory=dict)


class CorridorSignalRead(BaseModel):
    """One signal that intersects the selected route corridor."""

    traffic_signal_id: UUID
    signal_id: str
    sumo_edge_id: str = Field(
        description="SUMO bridge identity; never persisted onto a canonical route."
    )
    canonical_road_edge_id: UUID | None = None


class CorridorRead(BaseModel):
    status: CorridorStatus
    signals: list[CorridorSignalRead] = Field(default_factory=list)
    canonical_road_edge_count: int = 0
    sumo_edge_count: int = 0
    note: str


class SimulationRunEvidence(BaseModel):
    """One measured run of the pair."""

    simulation_id: UUID
    mode: SimulationMode
    status: SimulationStatus
    simulator: str
    network_id: str | None = None
    metrics: SimulationMetrics | None = None
    sumo_version: str | None = Field(
        default=None,
        description="Reported by the adapter. Null when the simulator is not SUMO.",
    )
    error_code: str | None = None
    error_message: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None


class SimulationComparisonEvidence(BaseModel):
    """Measured deltas. Every field is None unless both runs reported it."""

    travel_time_delta_seconds: float | None = None
    travel_time_improvement_percent: float | None = None
    stopped_time_delta_seconds: float | None = None
    stops_delta: int | None = None
    note: str = (
        "Improvement is the measured baseline-to-CLEARPATH reduction. It is a "
        "simulation observation and is never mixed with the optimizer score."
    )


class SimulationEvidenceRead(BaseModel):
    """The Phase 7 output: what was simulated, and what was measured."""

    simulation_pair_id: UUID = Field(
        description=(
            "Correlation id shared by both runs of the pair. It already exists "
            "on every SimulationRun, so a pair needs no new identifier."
        )
    )
    mission_id: UUID
    plan_id: UUID
    route_id: UUID | None = None
    vehicle_id: UUID | None = None
    plan_version: int | None = None

    baseline: SimulationRunEvidence
    clearpath: SimulationRunEvidence
    comparison: SimulationComparisonEvidence
    evidence_status: EvidenceStatus
    safety_status: SafetyStatus
    corridor: CorridorRead

    actions_requested: int = 0
    actions_approved: int = 0
    actions_executed: int = 0
    actions_rejected: int = 0

    seed: int
    max_simulation_seconds: int
    network: dict = Field(default_factory=dict)
    simulation_only: bool = True
    scope_notice: str = (
        "CLEARPATH evidence is produced by the SUMO/TraCI digital twin only. "
        "No real-world traffic signal is controlled, and this evidence does not "
        "authorise any action on real infrastructure."
    )
    generated_at: datetime

    @property
    def simulation_pair_id_str(self) -> str:
        return str(self.simulation_pair_id)
