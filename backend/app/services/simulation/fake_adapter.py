"""Deterministic adapter for tests and the explicitly synthetic dev fixture."""

import json
from pathlib import Path
from typing import Any

from app.schemas.simulation import SimulationMetrics, SimulationMode, SimulationScenario
from app.services.simulation.adapter import (
    EmergencyVehicleState,
    SimulationSignalError,
    SimulationVehicleError,
)

FIXTURE_PATH = (
    Path(__file__).resolve().parents[3]
    / "simulation"
    / "scenarios"
    / "development_fixture.json"
)


class FakeSimulationAdapter:
    name = "fake_simulation_adapter"

    def __init__(
        self,
        *,
        fixture_path: Path = FIXTURE_PATH,
        outcomes: dict[SimulationMode, SimulationMetrics] | None = None,
        fail_on_step: int | None = None,
    ) -> None:
        self.fixture_path = fixture_path
        self.outcomes = outcomes
        self.fail_on_step = fail_on_step
        self._scenario: SimulationScenario | None = None
        self._fixture: dict[str, Any] = {}
        self._index = -1
        self._signals: dict[str, str] = {}
        self.signal_commands: list[tuple[str, str]] = []
        self._closed = False
        self.metadata: dict[str, Any] = {}

    def start(self, scenario: SimulationScenario) -> None:
        self._fixture = json.loads(self.fixture_path.read_text(encoding="utf-8"))
        self.metadata = {
            "network_id": self._fixture["network_id"],
            "fixture_label": self._fixture["label"],
        }
        if scenario.network_id != self._fixture["network_id"]:
            raise ValueError("Fake adapter only accepts its named development fixture")
        if scenario.traffic_flows:
            raise ValueError("Development fixture does not implement dynamic traffic flows")
        unknown_edges = set(scenario.route_edge_ids) - set(self._fixture["edge_ids"])
        if unknown_edges:
            raise ValueError("Route contains edge IDs outside the development fixture")
        known_signals = {signal["signal_id"] for signal in self._fixture["signals"]}
        if any(signal.signal_id not in known_signals for signal in scenario.traffic_signals):
            raise ValueError("Scenario references a signal outside the development fixture")
        self._scenario = scenario
        self._index = -1
        self._signals = {
            signal.signal_id: signal.initial_phase
            for signal in scenario.traffic_signals
        }
        self.signal_commands = []
        self._closed = False

    def step(self) -> None:
        if self._closed:
            raise RuntimeError("Fake simulation is closed")
        self._index += 1
        if self.fail_on_step is not None and self._index + 1 == self.fail_on_step:
            raise RuntimeError("Configured deterministic fake adapter failure")

    def simulation_time_seconds(self) -> int:
        return min(self._index + 1, len(self._fixture["steps"]))

    def is_finished(self) -> bool:
        return self._index >= len(self._fixture["steps"]) - 1

    def get_vehicle_state(self, vehicle_id: str) -> EmergencyVehicleState | None:
        if self._scenario is None or vehicle_id != self._scenario.emergency_vehicle_id:
            raise SimulationVehicleError("Emergency vehicle is not configured")
        if self._index < 0:
            return None
        step = self._fixture["steps"][min(self._index, len(self._fixture["steps"]) - 1)]
        state = step["emergency_vehicle"]
        return EmergencyVehicleState(
            edge_id=state.get("edge_id"),
            speed_meters_per_second=state.get("speed_meters_per_second"),
            arrived=state["arrived"],
            waiting=state["waiting"],
            distances_to_signals_meters=state.get("distances_to_signals_meters", {}),
        )

    def get_signal_state(self, signal_id: str) -> str | None:
        if signal_id not in self._signals:
            raise SimulationSignalError("Traffic signal is not configured in this scenario")
        return self._signals[signal_id]

    def set_signal_state(self, signal_id: str, phase: str) -> None:
        if signal_id not in self._signals:
            raise SimulationSignalError("Traffic signal is not configured in this scenario")
        self._signals[signal_id] = phase
        self.signal_commands.append((signal_id, phase))

    def get_metrics(self, vehicle_id: str) -> SimulationMetrics:
        if self._scenario is None or vehicle_id != self._scenario.emergency_vehicle_id:
            raise SimulationVehicleError("Emergency vehicle is not configured")
        if self.outcomes is not None:
            return self.outcomes[self._scenario.mode].model_copy(deep=True)
        return SimulationMetrics.model_validate(
            self._fixture["metrics"][self._scenario.mode.value]
        )

    def close(self) -> None:
        self._closed = True