"""Shared synchronous simulation loop isolated from FastAPI's async event loop.

The runner owns orchestration only: it steps the adapter, asks
``ClearPathStrategy`` for proposals, sends every proposal through
``ClearPathSafetyGuard``, and guarantees that any approved pre-emption is
released before the adapter is closed. Simulator specifics stay in the adapter.
"""

import logging
from dataclasses import dataclass
from typing import Callable

from app.config import Settings, get_settings
from app.schemas.simulation import (
    SafetyDecision,
    SignalActionResult,
    SignalDefinition,
    SimulationMetrics,
    SimulationMode,
    SimulationScenario,
)
from app.services.simulation.adapter import (
    EmergencyVehicleState,
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

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SimulationExecution:
    metrics: SimulationMetrics
    actions: list[SignalActionResult]


AdapterFactory = Callable[[SimulationScenario], SimulationAdapter]
ActivePreemption = tuple[int, int]


class SimulationRunner:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.strategy = ClearPathStrategy(self.settings)
        self.guard = ClearPathSafetyGuard(self.settings)

    def execute(
        self, scenario: SimulationScenario, adapter: SimulationAdapter
    ) -> SimulationExecution:
        actions: list[SignalActionResult] = []
        active: dict[str, ActivePreemption] = {}
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

                if state is not None and state.arrived:
                    break
                if scenario.mode is SimulationMode.CLEARPATH and state is not None:
                    self._release_due(adapter, state, current_time, active, actions, signal_definitions)
                    self._request_actions(
                        adapter, scenario, state, current_time, active, actions
                    )
                if adapter.is_finished():
                    if not vehicle_seen:
                        raise SimulationVehicleError(
                            "Emergency vehicle never appeared in the simulation"
                        )
                    raise SimulationTerminatedError(
                        "Simulation ended before the emergency vehicle completed its route"
                    )
            else:
                raise SimulationTimeoutError(
                    "Simulation reached max_simulation_seconds before route completion"
                )

            final_state = adapter.get_vehicle_state(scenario.emergency_vehicle_id)
            if final_state is None or not final_state.arrived:
                raise SimulationTimeoutError(
                    "Simulation reached max_simulation_seconds before route completion"
                )
            metrics = adapter.get_metrics(scenario.emergency_vehicle_id)
            return SimulationExecution(metrics=metrics, actions=actions)
        finally:
            self._release_all(
                adapter, actions, active, signal_definitions, self._safe_time(adapter)
            )
            try:
                adapter.close()
            except Exception:
                logger.exception("Simulation adapter failed to close cleanly")

    def _request_actions(
        self,
        adapter: SimulationAdapter,
        scenario: SimulationScenario,
        state: EmergencyVehicleState,
        current_time: int,
        active: dict[str, ActivePreemption],
        actions: list[SignalActionResult],
    ) -> None:
        # A corridor signal is decided at most once per run. Re-arming is
        # deliberately not attempted so pre-emption can never become an
        # open-ended override of a simulated signal, and so a signal this
        # signal's metadata forbids is not re-proposed on every step.
        requested = set(active) | {
            result.action.signal_id for result in actions
        }
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
        state: EmergencyVehicleState,
        current_time: int,
        active: dict[str, ActivePreemption],
        actions: list[SignalActionResult],
        signals: dict[str, SignalDefinition],
    ) -> None:
        for signal_id, (activated_at, action_index) in tuple(active.items()):
            action_result = actions[action_index]
            signal = signals[signal_id]
            distance = state.distances_to_signals_meters.get(signal_id)
            expired = current_time - activated_at >= action_result.action.maximum_duration_seconds
            no_longer_tracked = distance is None or distance < 0
            if not (expired or no_longer_tracked):
                continue
            adapter.set_signal_state(signal_id, signal.release_phase)
            actions[action_index] = action_result.model_copy(
                update={"released_at_seconds": current_time}
            )
            del active[signal_id]

    def _release_all(
        self,
        adapter: SimulationAdapter,
        actions: list[SignalActionResult],
        active: dict[str, ActivePreemption],
        signals: dict[str, SignalDefinition],
        adapter_time: int,
    ) -> None:
        """Release every still-active pre-emption without masking a failure."""
        for signal_id, (_, action_index) in tuple(active.items()):
            try:
                adapter.set_signal_state(signal_id, signals[signal_id].release_phase)
                actions[action_index] = actions[action_index].model_copy(
                    update={"released_at_seconds": adapter_time}
                )
            except Exception:
                logger.exception(
                    "Failed to release simulated signal %s during teardown", signal_id
                )
            finally:
                active.pop(signal_id, None)

    @staticmethod
    def _safe_time(adapter: SimulationAdapter) -> int:
        try:
            return adapter.simulation_time_seconds()
        except Exception:
            logger.exception("Could not read simulation time during teardown")
            return 0