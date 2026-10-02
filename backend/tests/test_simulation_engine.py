"""Deterministic simulation adapter, CLEARPATH guard, and runner tests."""

from uuid import UUID

import pytest

from app.schemas.simulation import (
    SafetyDecision,
    SignalAction,
    SignalDefinition,
    SimulationMetrics,
    SimulationMode,
    SimulationScenario,
)
from app.services.simulation.adapter import (
    SimulationSignalError,
    SimulationTerminatedError,
    SimulationTimeoutError,
    SimulationUnavailableError,
    SimulationVehicleError,
)
from app.services.simulation.clearpath import ClearPathSafetyGuard
from app.services.simulation.fake_adapter import FakeSimulationAdapter
from app.services.simulation.runner import SimulationRunner
from app.services.simulation.service import SimulationService
from app.services.simulation.sumo_adapter import SumoTraCIAdapter
from app.config import Settings

SIGNAL_UUID = UUID(int=11)
ROUTE_UUID = UUID(int=12)
MISSION_UUID = UUID(int=13)
VEHICLE_UUID = UUID(int=14)


def signal_definition(**updates: object) -> SignalDefinition:
    base = {
        "traffic_signal_id": SIGNAL_UUID,
        "signal_id": "dev-tls-1",
        "edge_id": "dev-edge-1",
        "valid_phases": ["R", "G", "Y"],
        "preemption_phase": "G",
        "release_phase": "R",
        "safe_transitions": {"R": ["G"], "G": ["R"], "Y": ["R"]},
        "maximum_duration_seconds": 2,
        "initial_phase": "R",
    }
    return SignalDefinition(**{**base, **updates})


def scenario(
    mode: SimulationMode = SimulationMode.BASELINE,
    *,
    maximum_seconds: int = 10,
    signals: list[SignalDefinition] | None = None,
) -> SimulationScenario:
    return SimulationScenario(
        mission_id=MISSION_UUID,
        vehicle_id=VEHICLE_UUID,
        route_id=ROUTE_UUID,
        mode=mode,
        network_id="sentinel-development-test-network",
        route_edge_ids=["dev-edge-0", "dev-edge-1"],
        traffic_signals=signals if signals is not None else [signal_definition()],
        traffic_flows=[],
        emergency_vehicle_id=f"sentinel-{VEHICLE_UUID}",
        emergency_vehicle_configuration={"vehicle_type": "emergency"},
        seed=42,
        max_simulation_seconds=maximum_seconds,
        start_time="2026-10-02T00:00:00Z",
        correlation_id=UUID(int=15),
    )


def test_baseline_runs_without_signal_intervention_and_is_deterministic() -> None:
    first_adapter = FakeSimulationAdapter()
    first = SimulationRunner().execute(scenario(), first_adapter)
    second_adapter = FakeSimulationAdapter()
    second = SimulationRunner().execute(scenario(), second_adapter)

    assert first.actions == []
    assert first_adapter.signal_commands == []
    assert first.metrics == second.metrics
    assert first_adapter._closed is True
    assert second_adapter._closed is True


def test_clearpath_action_passes_guard_and_releases_signal() -> None:
    adapter = FakeSimulationAdapter()
    result = SimulationRunner().execute(scenario(SimulationMode.CLEARPATH), adapter)

    assert len(result.actions) == 1
    action_result = result.actions[0]
    assert action_result.decision is SafetyDecision.APPROVED
    assert action_result.released_at_seconds == 3
    assert adapter.signal_commands == [("dev-tls-1", "G"), ("dev-tls-1", "R")]
    assert adapter._closed is True


def test_safety_guard_rejects_unsafe_phase_transition_and_long_duration() -> None:
    active_scenario = scenario(SimulationMode.CLEARPATH)
    action = SignalAction(
        signal_id="dev-tls-1",
        traffic_signal_id=SIGNAL_UUID,
        requested_phase="G",
        activation_time_seconds=1,
        maximum_duration_seconds=3,
        reason="test approach",
        route_id=ROUTE_UUID,
    )
    guard = ClearPathSafetyGuard()
    too_long = guard.validate(action, active_scenario, "R")
    assert too_long.decision is SafetyDecision.REJECTED
    assert too_long.reason_code == "INTERVENTION_DURATION_EXCEEDED"

    unsafe_scenario = scenario(
        SimulationMode.CLEARPATH,
        signals=[signal_definition(safe_transitions={"R": [], "G": [], "Y": []})],
    )
    within_bound = action.model_copy(update={"maximum_duration_seconds": 2})
    unsafe = guard.validate(within_bound, unsafe_scenario, "R")
    assert unsafe.decision is SafetyDecision.REJECTED
    assert unsafe.reason_code == "UNSAFE_PHASE_TRANSITION"


def test_unmapped_signal_is_rejected_even_if_phase_is_valid() -> None:
    active_scenario = scenario(SimulationMode.CLEARPATH)
    action = SignalAction(
        signal_id="not-in-scenario",
        traffic_signal_id=UUID(int=99),
        requested_phase="G",
        activation_time_seconds=1,
        maximum_duration_seconds=1,
        reason="test",
        route_id=ROUTE_UUID,
    )
    decision = ClearPathSafetyGuard().validate(action, active_scenario, "R")
    assert decision.decision is SafetyDecision.REJECTED
    assert decision.reason_code == "SIGNAL_OUTSIDE_SIMULATION"


def test_clearpath_is_not_forced_to_outperform_baseline() -> None:
    outcomes = {
        SimulationMode.BASELINE: SimulationMetrics(
            emergency_vehicle_travel_time_seconds=4,
            stopped_time_seconds=1,
            number_of_stops=1,
            route_completed=True,
        ),
        SimulationMode.CLEARPATH: SimulationMetrics(
            emergency_vehicle_travel_time_seconds=6,
            stopped_time_seconds=3,
            number_of_stops=2,
            route_completed=True,
        ),
    }
    runner = SimulationRunner()
    baseline = runner.execute(
        scenario(SimulationMode.BASELINE), FakeSimulationAdapter(outcomes=outcomes)
    )
    clearpath = runner.execute(
        scenario(SimulationMode.CLEARPATH), FakeSimulationAdapter(outcomes=outcomes)
    )
    assert clearpath.metrics.emergency_vehicle_travel_time_seconds > baseline.metrics.emergency_vehicle_travel_time_seconds


def test_max_simulation_duration_times_out_and_still_closes_adapter() -> None:
    adapter = FakeSimulationAdapter()
    with pytest.raises(SimulationTimeoutError):
        SimulationRunner().execute(
            scenario(SimulationMode.BASELINE, maximum_seconds=2), adapter
        )
    assert adapter._closed is True


def test_unexpected_adapter_termination_is_reported_and_closed() -> None:
    class EarlyTerminationAdapter(FakeSimulationAdapter):
        def is_finished(self) -> bool:
            return True

    adapter = EarlyTerminationAdapter()
    with pytest.raises(SimulationTerminatedError):
        SimulationRunner().execute(scenario(), adapter)
    assert adapter._closed is True


def test_missing_signal_during_preemption_fails_and_closes_adapter() -> None:
    class MissingSignalAdapter(FakeSimulationAdapter):
        def get_signal_state(self, signal_id: str) -> str | None:
            return None

    adapter = MissingSignalAdapter()
    # Unknown phase makes the safety guard reject safely rather than writing a phase.
    result = SimulationRunner().execute(scenario(SimulationMode.CLEARPATH), adapter)
    assert result.actions[0].decision is SafetyDecision.REJECTED
    assert result.actions[0].reason_code == "CURRENT_PHASE_UNKNOWN"
    assert adapter.signal_commands == []
    assert adapter._closed is True


def test_fake_adapter_rejects_unknown_vehicle_and_signal() -> None:
    adapter = FakeSimulationAdapter()
    active_scenario = scenario()
    adapter.start(active_scenario)
    adapter.step()
    with pytest.raises(SimulationVehicleError):
        adapter.get_vehicle_state("unknown-vehicle")
    with pytest.raises(SimulationSignalError):
        adapter.get_signal_state("unknown-signal")
    adapter.close()


def test_development_fixture_rejects_dynamic_traffic_it_cannot_simulate() -> None:
    from app.schemas.simulation import TrafficFlow

    invalid_scenario = scenario().model_copy(
        update={
            "traffic_flows": [
                TrafficFlow(
                    demand_id="flow",
                    edge_ids=["dev-edge-0"],
                    vehicle_count=1,
                    depart_period_seconds=1,
                )
            ]
        }
    )
    with pytest.raises(ValueError, match="does not implement dynamic traffic flows"):
        FakeSimulationAdapter().start(invalid_scenario)


def test_traci_adapter_reports_unavailable_without_sumoconfig() -> None:
    adapter = SumoTraCIAdapter(Settings(sumo_config_path=None))
    with pytest.raises(SimulationUnavailableError) as error:
        adapter.start(scenario())
    assert error.value.code == "SIMULATOR_UNAVAILABLE"


def test_comparison_improvement_handles_zero_and_unavailable_baselines() -> None:
    assert SimulationService._improvement_percent(0, 0) is None
    assert SimulationService._improvement_percent(None, 1) is None
    assert SimulationService._improvement_percent(10, 8) == pytest.approx(20)
    assert SimulationService._improvement_percent(10, 12) == pytest.approx(-20)
