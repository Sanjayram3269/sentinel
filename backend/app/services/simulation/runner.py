"""Shared synchronous simulation loop isolated from FastAPI's async event loop."""

from dataclasses import dataclass
from typing import Callable

from app.config import Settings, get_settings
from app.schemas.simulation import (
    SafetyDecision,
    SignalActionResult,
    SimulationMetrics,
    SimulationMode,
    SimulationScenario,
)
from app.services.simulation.adapter import (
    SimulationAdapter,
    SimulationTerminatedError,
    SimulationTimeoutError,
    SimulationVehicleError,
)
from app.services.simulation.clearpath import (
    ClearPathSafetyGuard,
    ClearPathStrategy,
    rejected_result,
)


@dataclass(frozen=True)
class SimulationExecution:
    metrics: SimulationMetrics
    actions: list[SignalActionResult]


AdapterFactory = Callable[[SimulationScenario], SimulationAdapter]


class SimulationRunner:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.strategy = ClearPathStrategy(self.settings)
        self.guard = ClearPathSafetyGuard(self.settings)

    def execute(
        self, scenario: SimulationScenario, adapter: SimulationAdapter
    ) -> SimulationExecution:
        actions: list[SignalActionResult] = []
        active: dict[str, tuple[int, int]] = {}
        signal_definitions = {signal.signal_id: signal for signal in scenario.traffic_signals}
        vehicle_seen = False
        try:
            adapter.start(scenario)
            for _ in range(scenario.max_simulation_seconds):
                adapter.step()
                current_time = adapter.simulation_time_seconds()
                state = adapter.get_vehicle_state(scenario.emergency_vehicle_id)
                if state is not None:
                    vehicle_seen = True

                if scenario.mode is SimulationMode.CLEARPATH and state is not None:
                    self._release_due(adapter, state, current_time, active, actions, signal_definitions)
                    if not state.arrived:
                        self._request_actions(
                            adapter,
                            scenario,
                            state,
                            current_time,
                            active,
                            actions,
                        )

                if state is not None and state.arrived:
                    self._release_all(adapter, current_time, active, actions, signal_definitions)
                    break
                if current_time >= scenario.max_simulation_seconds:
                    raise SimulationTimeoutError(
                        "Simulation reached max_simulation_seconds before route completion"
                    )
                if adapter.is_finished():
                    if not vehicle_seen:
                        raise SimulationVehicleError(
                            "Emergency vehicle never appeared in the SUMO scenario"
                        )
                    raise SimulationTerminatedError(
                        "Simulation terminated before the emergency vehicle completed its route"
                    )
            else:
                raise SimulationTimeoutError(
                    "Simulation reached max_simulation_seconds before route completion"
                )

            metrics = adapter.get_metrics(scenario.emergency_vehicle_id)
            return SimulationExecution(metrics=metrics, actions=actions)
        finally:
            try:
                self._release_all(
                    adapter,
                    adapter.simulation_time_seconds(),
                    active,
                    actions,
                    signal_definitions,
                )
            finally:
                adapter.close()

    def _request_actions(
        self,
        adapter: SimulationAdapter,
        scenario: SimulationScenario,
        state,
        current_time: int,
        active: dict[str, tuple[int, int]],
        actions: list[SignalActionResult],
    ) -> None:
        requested = set(active)
        requested.update(
            result.action.signal_id
            for result in actions
            if result.decision is SafetyDecision.APPROVED
        )
        for action in self.strategy.determine_actions(
            scenario, state, current_time, requested
        ):
            current_phase = adapter.get_signal_state(action.signal_id)
            verdict = self.guard.validate(action, scenario, current_phase)
            result = rejected_result(action, verdict)
            actions.append(result)
            if verdict.decision is SafetyDecision.REJECTED:
                continue
            adapter.set_signal_state(action.signal_id, action.requested_phase)
            active[action.signal_id] = (current_time, len(actions) - 1)

    def _release_due(
        self,
        adapter: SimulationAdapter,
        state,
        current_time: int,
        active: dict[str, tuple[int, int]],
        actions: list[SignalActionResult],
        signals,
    ) -> None:
        for signal_id, (activated_at, action_index) in tuple(active.items()):
            action_result = actions[action_index]
            signal = signals[signal_id]
            distance = state.distances_to_signals_meters.get(signal_id)
            if (
                current_time - activated_at >= action_result.action.maximum_duration_seconds
                or distance is None
                or distance < 0
            ):
                adapter.set_signal_state(signal_id, signal.release_phase)
                actions[action_index] = action_result.model_copy(
                    update={"released_at_seconds": current_time}
                )
                del active[signal_id]

    def _release_all(
        self,
        adapter: SimulationAdapter,
        current_time: int,
        active: dict[str, tuple[int, int]],
        actions: list[SignalActionResult],
        signals,
    ) -> None:
        for signal_id, (_, action_index) in tuple(active.items()):
            try:
                adapter.set_signal_state(signal_id, signals[signal_id].release_phase)
                actions[action_index] = actions[action_index].model_copy(
                    update={"released_at_seconds": current_time}
                )
            finally:
                del active[signal_id]