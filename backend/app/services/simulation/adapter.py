"""Synchronous simulator contract isolated from async API and persistence code."""

from dataclasses import dataclass, field
from typing import Protocol

from app.schemas.simulation import SimulationMetrics, SimulationScenario


@dataclass(frozen=True)
class EmergencyVehicleState:
    edge_id: str | None
    speed_meters_per_second: float | None
    arrived: bool
    waiting: bool
    distances_to_signals_meters: dict[str, float] = field(default_factory=dict)


class SimulationAdapter(Protocol):
    name: str

    def start(self, scenario: SimulationScenario) -> None: ...

    def step(self) -> None: ...

    def simulation_time_seconds(self) -> int: ...

    def is_finished(self) -> bool: ...

    def get_vehicle_state(self, vehicle_id: str) -> EmergencyVehicleState | None: ...

    def get_signal_state(self, signal_id: str) -> str | None: ...

    def set_signal_state(self, signal_id: str, phase: str) -> None: ...

    def get_metrics(self, vehicle_id: str) -> SimulationMetrics: ...

    def close(self) -> None: ...


class SimulationAdapterError(RuntimeError):
    """Base adapter failure with a safe API-facing error code."""

    code = "SIMULATOR_FAILURE"


class SimulationUnavailableError(SimulationAdapterError):
    code = "SIMULATOR_UNAVAILABLE"


class SimulationConnectionError(SimulationAdapterError):
    code = "SIMULATOR_CONNECTION_FAILED"


class SimulationVehicleError(SimulationAdapterError):
    code = "SIMULATION_VEHICLE_MISSING"


class SimulationSignalError(SimulationAdapterError):
    code = "SIMULATION_SIGNAL_MISSING"


class SimulationTimeoutError(SimulationAdapterError):
    code = "SIMULATION_TIMEOUT"


class SimulationTerminatedError(SimulationAdapterError):
    code = "SIMULATION_TERMINATED"