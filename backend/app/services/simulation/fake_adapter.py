"""Deterministic adapter backed by the bundled development fixture.

The fixture is a **synthetic** scenario used for development and tests when SUMO
is not configured. It is not a representation of any real road network and its
numbers must never be presented as a traffic-performance result.

The adapter replays a declared, fixed trace of the emergency vehicle and
*measures* the outcome of that replay (travel time, stopped time, stop count,
mean speed, completion). It deliberately does not model how traffic responds to
signal control, so a baseline and a CLEARPATH run of the same fixture produce
the same measured metrics. Real mode sensitivity requires the SUMO adapter.
"""

import json
from pathlib import Path
from typing import Any

from app.schemas.simulation import SimulationMetrics, SimulationScenario
from app.services.simulation.adapter import (
    EmergencyVehicleState,
    SimulationConfigurationError,
    SimulationSignalError,
    SimulationStepError,
    SimulationVehicleError,
)

FIXTURE_PATH = (
    Path(__file__).resolve().parents[3]
    / "simulation"
    / "scenarios"
    / "development_fixture.json"
)
STOPPED_SPEED_THRESHOLD = 0.1


class FakeSimulationAdapter:
    """Replay a declared fixture trace and measure what actually happened."""

    name = "fake_simulation_adapter"

    def __init__(
        self,
        *,
        fixture_path: Path = FIXTURE_PATH,
        fail_on_step: int | None = None,
        metric_overrides: SimulationMetrics | None = None,
        departure_step: int | None = None,
    ) -> None:
        self.fixture_path = fixture_path
        self.fail_on_step = fail_on_step
        # Test-only override of the measured outcome. Production code paths never
        # pass this, so a persisted run can never report numbers that the adapter
        # did not actually measure.
        self.metric_overrides = metric_overrides
        self.departure_step = departure_step
        self.metadata: dict[str, Any] = {}
        self.signal_commands: list[tuple[str, str]] = []
        self._fixture: dict[str, Any] = {}
        self._scenario: SimulationScenario | None = None
        self._index = -1
        self._signals: dict[str, str] = {}
        self._speed_samples: list[float] = []
        self._stopped_seconds = 0.0
        self._stop_count = 0
        self._was_moving = False
        self._arrival_step: int | None = None
        self._closed = False

    # -- lifecycle ---------------------------------------------------------

    def start(self, scenario: SimulationScenario) -> None:
        self._fixture = json.loads(self.fixture_path.read_text(encoding="utf-8"))
        self.metadata = {
            "network_id": self._fixture["network_id"],
            "fixture_label": self._fixture["label"],
            "fixture_model": self._fixture["model"],
        }
        if scenario.network_id != self._fixture["network_id"]:
            raise SimulationConfigurationError(
                "Development fixture only accepts its named network"
            )
        if scenario.traffic_flows:
            raise SimulationConfigurationError(
                "Development fixture does not implement dynamic traffic flows"
            )
        if set(scenario.route_edge_ids) - set(self._fixture["edge_ids"]):
            raise SimulationConfigurationError(
                "Route contains edge IDs outside the development fixture"
            )
        known_signals = {signal["signal_id"] for signal in self._fixture["signals"]}
        unknown_signals = {
            signal.signal_id for signal in scenario.traffic_signals
        } - known_signals
        if unknown_signals:
            raise SimulationConfigurationError(
                "Scenario references a signal outside the development fixture"
            )
        self._scenario = scenario
        self._index = -1
        self._arrival_step = None
        self._speed_samples = []
        self._stopped_seconds = 0.0
        self._stop_count = 0
        self._was_moving = False
        self._signals = {
            signal.signal_id: signal.initial_phase
            for signal in scenario.traffic_signals
        }
        self.signal_commands = []
        self._closed = False

    def step(self) -> None:
        if self._closed:
            raise SimulationStepError("Development fixture simulation is closed")
        self._index += 1
        if self.fail_on_step is not None and self._index + 1 == self.fail_on_step:
            raise SimulationStepError(
                "Injected deterministic development fixture failure"
            )
        self._sample_step(self._index)

    def close(self) -> None:
        self._closed = True

    # -- declared trace ----------------------------------------------------

    @property
    def _steps(self) -> list[dict[str, Any]]:
        return self._fixture["steps"]

    def _sample_step(self, index: int) -> None:
        if index >= len(self._steps):
            return
        step = self._steps[index]
        if step.get("arrived"):
            self._arrival_step = index + 1
            return
        speed = float(step["speed_meters_per_second"])
        self._speed_samples.append(speed)
        stopped = speed <= STOPPED_SPEED_THRESHOLD
        if stopped:
            self._stopped_seconds += 1.0
            if self._was_moving:
                self._stop_count += 1
        self._was_moving = not stopped

    def _declared_departure_step(self) -> int:
        return int(self._fixture.get("departure_step", 0))

    def _departure_step(self) -> int:
        if self.departure_step is not None:
            return self.departure_step
        return self._declared_departure_step()

    # -- adapter contract --------------------------------------------------

    def simulation_time_seconds(self) -> int:
        return max(self._index + 1, 0)

    def is_finished(self) -> bool:
        """The declared trace is exhausted, so no further progress is possible."""
        return self._index >= len(self._steps) - 1

    def get_vehicle_state(self, vehicle_id: str) -> EmergencyVehicleState | None:
        if self._scenario is None or vehicle_id != self._scenario.emergency_vehicle_id:
            raise SimulationVehicleError("Emergency vehicle is not configured")
        if self._index < self._departure_step():
            return None
        if self._arrival_step is not None:
            return EmergencyVehicleState(
                edge_id=self._steps[-1]["edge_id"],
                speed_meters_per_second=0.0,
                arrived=True,
                waiting=False,
                distances_to_signals_meters={},
            )
        step = self._steps[min(self._index, len(self._steps) - 1)]
        speed = float(step["speed_meters_per_second"])
        return EmergencyVehicleState(
            edge_id=step["edge_id"],
            speed_meters_per_second=speed,
            arrived=False,
            waiting=speed <= STOPPED_SPEED_THRESHOLD,
            distances_to_signals_meters=dict(
                step.get("distances_to_signals_meters", {})
            ),
        )

    def get_signal_state(self, signal_id: str) -> str | None:
        if signal_id not in self._signals:
            raise SimulationSignalError(
                "Traffic signal is not configured in this scenario"
            )
        return self._signals[signal_id]

    def set_signal_state(self, signal_id: str, phase: str) -> None:
        if self._closed:
            raise SimulationStepError("Development fixture simulation is closed")
        if signal_id not in self._signals:
            raise SimulationSignalError(
                "Traffic signal is not configured in this scenario"
            )
        self._signals[signal_id] = phase
        self.signal_commands.append((signal_id, phase))

    def get_metrics(self, vehicle_id: str) -> SimulationMetrics:
        if self._scenario is None or vehicle_id != self._scenario.emergency_vehicle_id:
            raise SimulationVehicleError("Emergency vehicle is not configured")
        if self.metric_overrides is not None:
            return self.metric_overrides.model_copy(deep=True)
        return self._measured_metrics()

    def _measured_metrics(self) -> SimulationMetrics:
        """Metrics measured from the replay, never canned per simulation mode."""
        average_speed = (
            sum(self._speed_samples) / len(self._speed_samples)
            if self._speed_samples
            else None
        )
        if self._arrival_step is None:
            return SimulationMetrics(
                route_completed=False,
                simulation_duration_seconds=float(self.simulation_time_seconds()),
                stopped_time_seconds=self._stopped_seconds,
                number_of_stops=self._stop_count,
                average_speed_meters_per_second=average_speed,
            )
        return SimulationMetrics(
            emergency_vehicle_travel_time_seconds=float(self._arrival_step),
            # The fixture has no SUMO time-loss model, so this stays unavailable
            # rather than being reported as a simulated delay.
            total_delay_seconds=None,
            stopped_time_seconds=self._stopped_seconds,
            number_of_stops=self._stop_count,
            average_speed_meters_per_second=average_speed,
            route_completed=True,
            simulation_duration_seconds=float(self._arrival_step),
            background_average_delay_seconds=None,
            background_vehicle_throughput=None,
        )
