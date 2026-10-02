"""Synchronous simulator contract isolated from async API and persistence code."""

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from app.schemas.simulation import SimulationMetrics, SimulationScenario


@dataclass(frozen=True)
class EmergencyVehicleState:
    edge_id: str | None
    speed_meters_per_second: float | None
    arrived: bool
    waiting: bool
    distances_to_signals_meters: dict[str, float] = field(default_factory=dict)


@runtime_checkable
class SimulationAdapter(Protocol):
    """Synchronous simulator contract.

    Implementations are replaceable and must not assume a particular simulator.
    Instances are single-use: ``start()`` prepares a run and ``close()`` releases
    every resource the adapter acquired, including on failure.
    """

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


def adapter_metadata(adapter: object) -> dict[str, Any]:
    """Read optional adapter provenance without assuming a specific attribute.

    Adapters expose a public ``metadata`` mapping; anything else is reported as
    empty rather than leaking internal state into a persisted run.
    """
    value = getattr(adapter, "metadata", None)
    return dict(value) if isinstance(value, dict) else {}


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


class SimulationStepError(SimulationAdapterError):
    code = "SIMULATION_STEP_FAILED"


class SimulationConfigurationError(SimulationAdapterError):
    code = "SIMULATION_CONFIGURATION_INVALID"