"""Deterministic CLEARPATH strategy and digital-twin-only safety guard."""

from dataclasses import dataclass
from uuid import UUID

from app.config import Settings, get_settings
from app.schemas.simulation import (
    SafetyDecision,
    SignalAction,
    SignalActionResult,
    SimulationScenario,
)
from app.services.simulation.adapter import EmergencyVehicleState


@dataclass(frozen=True)
class SafetyVerdict:
    decision: SafetyDecision
    reason_code: str
    explanation: str


class ClearPathSafetyGuard:
    """Validate every requested simulated signal phase transition."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def validate(
        self,
        action: SignalAction,
        scenario: SimulationScenario,
        current_phase: str | None,
    ) -> SafetyVerdict:
        signal = next(
            (
                item
                for item in scenario.traffic_signals
                if item.traffic_signal_id == action.traffic_signal_id
                and item.signal_id == action.signal_id
            ),
            None,
        )
        if signal is None:
            return self._reject("SIGNAL_OUTSIDE_SIMULATION", "Signal is not configured in this simulation")
        if action.route_id != scenario.route_id or signal.edge_id not in scenario.route_edge_ids:
            return self._reject("SIGNAL_OUTSIDE_CORRIDOR", "Signal is not mapped to this route corridor")
        if current_phase is None or current_phase not in signal.valid_phases:
            return self._reject("CURRENT_PHASE_UNKNOWN", "Current simulated signal phase is unknown or invalid")
        if action.requested_phase not in signal.valid_phases:
            return self._reject("INVALID_REQUESTED_PHASE", "Requested phase is not valid for this signal")
        if action.requested_phase == signal.release_phase:
            return self._reject("NO_PREEMPTION_PHASE", "Requested phase does not enter the configured pre-emption phase")
        duration_cap = min(
            signal.maximum_duration_seconds,
            self.settings.simulation_max_signal_preemption_seconds,
            scenario.max_simulation_seconds,
        )
        if action.maximum_duration_seconds <= 0 or action.maximum_duration_seconds > duration_cap:
            return self._reject("INTERVENTION_DURATION_EXCEEDED", "Intervention duration is not within the configured bound")
        if action.requested_phase not in signal.safe_transitions.get(current_phase, []):
            return self._reject("UNSAFE_PHASE_TRANSITION", "Configured signal metadata does not permit this transition")
        if signal.release_phase not in signal.safe_transitions.get(action.requested_phase, []):
            return self._reject("UNSAFE_RELEASE_TRANSITION", "Configured metadata does not permit safe release")
        return SafetyVerdict(
            SafetyDecision.APPROVED,
            "APPROVED_SIMULATION_ONLY",
            "Transition is allowed by this simulation signal's configured phase metadata",
        )

    @staticmethod
    def _reject(code: str, explanation: str) -> SafetyVerdict:
        return SafetyVerdict(SafetyDecision.REJECTED, code, explanation)


class ClearPathStrategy:
    """Request bounded pre-emption only for an approaching corridor signal."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def determine_actions(
        self,
        scenario: SimulationScenario,
        state: EmergencyVehicleState,
        current_time_seconds: int,
        already_requested: set[str],
    ) -> list[SignalAction]:
        actions = []
        for signal in scenario.traffic_signals:
            distance = state.distances_to_signals_meters.get(signal.signal_id)
            if (
                signal.signal_id in already_requested
                or signal.edge_id not in scenario.route_edge_ids
                or distance is None
                or distance < 0
                or distance > self.settings.simulation_approach_distance_meters
            ):
                continue
            actions.append(
                SignalAction(
                    signal_id=signal.signal_id,
                    traffic_signal_id=signal.traffic_signal_id,
                    requested_phase=signal.preemption_phase,
                    activation_time_seconds=current_time_seconds,
                    maximum_duration_seconds=min(
                        signal.maximum_duration_seconds,
                        self.settings.simulation_max_signal_preemption_seconds,
                    ),
                    reason="Emergency vehicle is approaching a mapped corridor signal",
                    route_id=scenario.route_id,
                )
            )
        return actions


def rejected_result(action: SignalAction, verdict: SafetyVerdict) -> SignalActionResult:
    return SignalActionResult(
        action=action,
        decision=verdict.decision,
        reason_code=verdict.reason_code,
        explanation=verdict.explanation,
    )