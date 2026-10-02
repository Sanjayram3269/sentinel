"""Deterministic simulation, CLEARPATH, and comparison tests.

Everything here runs against the synthetic development fixture, so the suite
passes with no SUMO installation and no database.
"""

import json
from uuid import UUID

import pytest

from app.config import Settings
from app.schemas.simulation import (
    SafetyDecision,
    SignalAction,
    SignalDefinition,
    SimulationMetrics,
    SimulationMode,
    SimulationScenario,
    TrafficFlow,
)
from app.services.simulation.adapter import (
    EmergencyVehicleState,
    SimulationAdapter,
    SimulationConfigurationError,
    SimulationConnectionError,
    SimulationSignalError,
    SimulationStepError,
    SimulationTerminatedError,
    SimulationTimeoutError,
    SimulationUnavailableError,
    SimulationVehicleError,
)
from app.services.simulation.clearpath import ClearPathSafetyGuard, ClearPathStrategy
from app.services.simulation.fake_adapter import FIXTURE_PATH, FakeSimulationAdapter
from app.services.simulation.runner import SimulationRunner
from app.services.simulation.service import build_comparison_metrics
from app.services.simulation.scenario_builder import ScenarioBuilder
from app.services.simulation.sumo_adapter import (
    SumoTraCIAdapter,
    edge_link_indices,
    verify_preemption_phase_grants_green,
)

SIGNAL_UUID = UUID(int=11)
ROUTE_UUID = UUID(int=12)
MISSION_UUID = UUID(int=13)
VEHICLE_UUID = UUID(int=14)
CORRELATION_UUID = UUID(int=15)
DEVELOPMENT_NETWORK_ID = "sentinel-development-test-network"


def signal_definition(**updates: object) -> SignalDefinition:
    base: dict[str, object] = {
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
    seed: int = 42,
) -> SimulationScenario:
    return SimulationScenario(
        mission_id=MISSION_UUID,
        vehicle_id=VEHICLE_UUID,
        route_id=ROUTE_UUID,
        mode=mode,
        network_id=DEVELOPMENT_NETWORK_ID,
        route_edge_ids=["dev-edge-0", "dev-edge-1"],
        traffic_signals=signals if signals is not None else [signal_definition()],
        traffic_flows=[],
        emergency_vehicle_id=f"sentinel-{VEHICLE_UUID}",
        emergency_vehicle_configuration={"sumo_vehicle_type_id": "emergency"},
        seed=seed,
        max_simulation_seconds=maximum_seconds,
        start_time="2026-10-02T00:00:00Z",
        correlation_id=CORRELATION_UUID,
    )


def clearpath_action(**updates: object) -> SignalAction:
    base: dict[str, object] = {
        "signal_id": "dev-tls-1",
        "traffic_signal_id": SIGNAL_UUID,
        "requested_phase": "G",
        "activation_time_seconds": 1,
        "maximum_duration_seconds": 2,
        "reason": "unit test approach",
        "route_id": ROUTE_UUID,
    }
    return SignalAction(**{**base, **updates})


# -- A. scenario / signal definition validation ---------------------------


@pytest.mark.parametrize(
    "updates, expected",
    [
        ({"preemption_phase": "X"}, "preemption_phase must be one of valid_phases"),
        ({"release_phase": "G"}, "preemption_phase and release_phase must differ"),
        (
            {"safe_transitions": {"R": ["G"], "G": ["R"], "Y": ["R"], "X": ["R"]}},
            "unknown phase",
        ),
        (
            {"safe_transitions": {"R": [], "G": ["R"], "Y": ["R"]}},
            "out of initial_phase",
        ),
        (
            {"safe_transitions": {"R": ["G"], "G": [], "Y": ["R"]}},
            "preemption_phase -> release_phase",
        ),
    ],
)
def test_signal_definition_rejects_inconsistent_phase_metadata(
    updates: dict[str, object], expected: str
) -> None:
    with pytest.raises(ValueError, match=expected):
        signal_definition(**updates)


def test_signal_definition_rejects_unknown_fields() -> None:
    with pytest.raises(ValueError):
        signal_definition(sumo_phase_index=3)


def test_development_network_rejects_unknown_edges() -> None:
    with pytest.raises(Exception) as error:
        ScenarioBuilder()._validate_network(
            DEVELOPMENT_NETWORK_ID, ["dev-edge-0", "invented-edge"], []
        )
    assert "not present in the development fixture" in str(error.value)


def test_development_network_rejects_dynamic_traffic_flows() -> None:
    flow = TrafficFlow(
        demand_id="flow",
        edge_ids=["dev-edge-0"],
        vehicle_count=1,
        depart_period_seconds=1.0,
    )
    with pytest.raises(Exception) as error:
        ScenarioBuilder()._validate_network(DEVELOPMENT_NETWORK_ID, ["dev-edge-0"], [flow])
    assert "does not accept dynamic traffic flows" in str(error.value)


def test_unconfigured_network_is_rejected_without_fabrication() -> None:
    builder = ScenarioBuilder(Settings(sumo_network_id=None))
    with pytest.raises(Exception) as error:
        builder._validate_network("some-real-city-network", ["edge-a"], [])
    assert "network mapping is unavailable" in str(error.value)


def test_configured_sumo_network_accepts_any_explicit_edge_mapping() -> None:
    builder = ScenarioBuilder(Settings(sumo_network_id="team-network-v1"))
    builder._validate_network("team-network-v1", ["edge-a", "edge-b"], [])


# -- B/F. baseline execution ---------------------------------------------


def test_baseline_never_requests_or_writes_a_signal() -> None:
    adapter = FakeSimulationAdapter()
    result = SimulationRunner().execute(scenario(SimulationMode.BASELINE), adapter)

    assert result.actions == []
    assert adapter.signal_commands == []
    assert adapter._closed is True


# -- C/G/K. CLEARPATH execution, action generation, and release -----------


def test_clearpath_generates_one_approved_action_and_releases_the_signal() -> None:
    adapter = FakeSimulationAdapter()
    result = SimulationRunner().execute(scenario(SimulationMode.CLEARPATH), adapter)

    assert len(result.actions) == 1
    action_result = result.actions[0]
    assert action_result.decision is SafetyDecision.APPROVED
    assert action_result.reason_code == "APPROVED_SIMULATION_ONLY"
    assert action_result.action.requested_phase == "G"
    assert action_result.released_at_seconds == 3
    assert adapter.signal_commands == [("dev-tls-1", "G"), ("dev-tls-1", "R")]


def test_clearpath_never_leaves_a_signal_pre_empted_at_teardown() -> None:
    adapter = _NeverArriving()
    with pytest.raises(SimulationTerminatedError):
        SimulationRunner().execute(
            scenario(SimulationMode.CLEARPATH, maximum_seconds=60), adapter
        )
    assert adapter.signal_commands[-1] == ("dev-tls-1", "R")
    assert adapter._closed is True


class _NeverArriving(FakeSimulationAdapter):
    """Same fixture, but the simulator stops while the signal is pre-empted."""

    def is_finished(self) -> bool:
        return self._index >= 2


# -- D/E. determinism ----------------------------------------------------


def test_same_fixture_and_seed_produce_identical_measurements() -> None:
    first = SimulationRunner().execute(scenario(), FakeSimulationAdapter())
    second = SimulationRunner().execute(scenario(), FakeSimulationAdapter())

    assert first.metrics == second.metrics
    assert first.actions == second.actions
    assert first.metrics.emergency_vehicle_travel_time_seconds == 4
    assert first.metrics.stopped_time_seconds == 1
    assert first.metrics.number_of_stops == 1
    assert first.metrics.average_speed_meters_per_second == pytest.approx(3.0)


def test_fixture_metrics_are_measured_not_canned_per_mode() -> None:
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    assert "metrics" not in fixture
    assert "TEST NETWORK ONLY" in fixture["label"]

    baseline = SimulationRunner().execute(scenario(), FakeSimulationAdapter())
    clearpath = SimulationRunner().execute(
        scenario(SimulationMode.CLEARPATH), FakeSimulationAdapter()
    )
    # The fixture declares no traffic response to signal control, so both modes
    # must measure identically rather than claim a fabricated improvement.
    assert baseline.metrics == clearpath.metrics


# -- H/I/J. safety guard rejections --------------------------------------


@pytest.mark.parametrize(
    "current_phase, updates, expected_code",
    [
        ("R", {}, "MODE_NOT_CLEARPATH"),
        ("R", {"route_id": UUID(int=99)}, "SIGNAL_OUTSIDE_CORRIDOR"),
        ("R", {"requested_phase": "Y"}, "NO_PREEMPTION_PHASE"),
        ("R", {"requested_phase": "R"}, "NO_PREEMPTION_PHASE"),
        (None, {}, "CURRENT_PHASE_UNKNOWN"),
        ("X", {}, "CURRENT_PHASE_UNKNOWN"),
        ("R", {"maximum_duration_seconds": 3}, "INTERVENTION_DURATION_EXCEEDED"),
        ("R", {"signal_id": "not-in-scenario"}, "SIGNAL_OUTSIDE_SIMULATION"),
    ],
)
def test_safety_guard_rejects_with_machine_readable_reason(
    current_phase: str | None, updates: dict[str, object], expected_code: str
) -> None:
    mode = (
        SimulationMode.BASELINE
        if expected_code == "MODE_NOT_CLEARPATH"
        else SimulationMode.CLEARPATH
    )
    verdict = ClearPathSafetyGuard().validate(
        clearpath_action(**updates), scenario(mode), current_phase
    )
    assert verdict.decision is SafetyDecision.REJECTED
    assert verdict.reason_code == expected_code
    assert verdict.explanation


def test_safety_guard_rejects_transition_without_metadata_permission() -> None:
    # The signal is running its amber phase when the action is proposed, and the
    # configured metadata does not permit amber -> pre-emption green.
    verdict = ClearPathSafetyGuard().validate(
        clearpath_action(), scenario(SimulationMode.CLEARPATH), current_phase="Y"
    )
    assert verdict.reason_code == "UNSAFE_PHASE_TRANSITION"


def test_safety_guard_rejects_release_transition_without_metadata() -> None:
    # Defence in depth: even if corrupted metadata reaches the guard at runtime,
    # a pre-emption that cannot be released is refused.
    corrupted = signal_definition()
    corrupted.safe_transitions = {"R": ["G"], "G": ["Y"], "Y": ["R"]}
    unsafe = scenario(SimulationMode.CLEARPATH).model_copy(
        update={"traffic_signals": [corrupted]}
    )
    verdict = ClearPathSafetyGuard().validate(clearpath_action(), unsafe, current_phase="R")
    assert verdict.reason_code == "UNSAFE_RELEASE_TRANSITION"


def test_safety_guard_bounds_duration_by_server_configuration() -> None:
    guard = ClearPathSafetyGuard(Settings(simulation_max_signal_preemption_seconds=1))
    verdict = guard.validate(clearpath_action(), scenario(SimulationMode.CLEARPATH), "R")
    assert verdict.reason_code == "INTERVENTION_DURATION_EXCEEDED"


# -- strategy eligibility ------------------------------------------------


def test_strategy_ignores_non_corridor_signals_and_far_signals() -> None:
    strategy = ClearPathStrategy()
    far = EmergencyVehicleState("dev-edge-0", 5.0, False, False, {"dev-tls-1": 900.0})
    assert strategy.determine_actions(scenario(SimulationMode.CLEARPATH), far, 1, set()) == []

    off_corridor = scenario(
        SimulationMode.CLEARPATH, signals=[signal_definition(edge_id="dev-edge-9")]
    )
    near = EmergencyVehicleState("dev-edge-0", 5.0, False, False, {"dev-tls-1": 50.0})
    assert strategy.determine_actions(off_corridor, near, 1, set()) == []

    corridor = scenario(SimulationMode.CLEARPATH)
    assert strategy.determine_actions(corridor, near, 1, set()) != []
    assert strategy.determine_actions(corridor, near, 1, {"dev-tls-1"}) == []
    assert strategy.determine_actions(scenario(), near, 1, set()) == []


def test_strategy_does_not_rearm_an_already_requested_signal() -> None:
    strategy = ClearPathStrategy()
    state = EmergencyVehicleState("dev-edge-0", 5.0, False, False, {"dev-tls-1": 50.0})
    first = strategy.determine_actions(
        scenario(SimulationMode.CLEARPATH), state, 1, set()
    )
    second = strategy.determine_actions(
        scenario(SimulationMode.CLEARPATH), state, 2, {"dev-tls-1"}
    )
    assert len(first) == 1
    assert second == []


def test_strategy_ignores_an_arrived_vehicle() -> None:
    arrived = EmergencyVehicleState(None, 0.0, True, False, {"dev-tls-1": 5.0})
    assert (
        ClearPathStrategy().determine_actions(
            scenario(SimulationMode.CLEARPATH), arrived, 5, set()
        )
        == []
    )


# -- L/M/N/O. failure modes ----------------------------------------------


def test_missing_vehicle_fails_the_run_and_closes_the_adapter() -> None:
    adapter = FakeSimulationAdapter(departure_step=99)
    with pytest.raises(SimulationVehicleError) as error:
        SimulationRunner().execute(scenario(), adapter)
    assert error.value.code == "SIMULATION_VEHICLE_MISSING"
    assert adapter._closed is True


def test_missing_signal_phase_is_rejected_without_writing_a_phase() -> None:
    class MissingSignalAdapter(FakeSimulationAdapter):
        def get_signal_state(self, signal_id: str) -> str | None:
            return None

    adapter = MissingSignalAdapter()
    result = SimulationRunner().execute(scenario(SimulationMode.CLEARPATH), adapter)
    assert result.actions[0].decision is SafetyDecision.REJECTED
    assert result.actions[0].reason_code == "CURRENT_PHASE_UNKNOWN"
    assert adapter.signal_commands == []
    assert adapter._closed is True


def test_a_rejected_signal_is_decided_once_and_not_re_proposed_every_step() -> None:
    """A signal the metadata forbids must not be re-requested on each step."""

    forbidden = signal_definition(
        safe_transitions={"R": ["Y"], "G": ["R"], "Y": ["R"]}
    )
    adapter = FakeSimulationAdapter()
    result = SimulationRunner().execute(
        scenario(SimulationMode.CLEARPATH, signals=[forbidden], maximum_seconds=30),
        adapter,
    )

    assert result.actions, "the forbidden transition must still be reported once"
    assert all(a.decision is SafetyDecision.REJECTED for a in result.actions)
    assert {a.reason_code for a in result.actions} == {"UNSAFE_PHASE_TRANSITION"}
    assert len(result.actions) == 1
    assert adapter.signal_commands == []


def test_missing_signal_write_closes_the_adapter_and_preserves_the_error() -> None:
    class FailingSignalAdapter(FakeSimulationAdapter):
        def set_signal_state(self, signal_id: str, phase: str) -> None:
            raise SimulationSignalError("signal vanished mid-run")

    adapter = FailingSignalAdapter()
    with pytest.raises(SimulationSignalError):
        SimulationRunner().execute(scenario(SimulationMode.CLEARPATH), adapter)
    assert adapter._closed is True


def test_simulator_failure_surfaces_an_explicit_error_code() -> None:
    adapter = FakeSimulationAdapter(fail_on_step=2)
    with pytest.raises(SimulationStepError) as error:
        SimulationRunner().execute(scenario(), adapter)
    assert error.value.code == "SIMULATION_STEP_FAILED"
    assert adapter._closed is True


def test_horizon_timeout_closes_the_adapter() -> None:
    adapter = FakeSimulationAdapter()
    with pytest.raises(SimulationTimeoutError):
        SimulationRunner().execute(
            scenario(SimulationMode.BASELINE, maximum_seconds=2), adapter
        )
    assert adapter._closed is True


def test_unexpected_termination_is_reported_and_closed() -> None:
    class EarlyTerminationAdapter(FakeSimulationAdapter):
        def is_finished(self) -> bool:
            return True

    adapter = EarlyTerminationAdapter()
    with pytest.raises(SimulationTerminatedError):
        SimulationRunner().execute(scenario(), adapter)
    assert adapter._closed is True


# -- Z. lifecycle cleanup ------------------------------------------------


def test_teardown_failure_does_not_mask_the_original_error() -> None:
    class BrokenTeardownAdapter(FakeSimulationAdapter):
        def is_finished(self) -> bool:
            return True

        def simulation_time_seconds(self) -> int:
            raise RuntimeError("connection already gone")

        def close(self) -> None:
            raise RuntimeError("close also failed")

    # The recorded simulation failure must survive a broken teardown.
    with pytest.raises(RuntimeError, match="connection already gone"):
        SimulationRunner().execute(scenario(SimulationMode.CLEARPATH), BrokenTeardownAdapter())


def test_adapter_rejects_operations_after_close() -> None:
    adapter = FakeSimulationAdapter()
    adapter.start(scenario())
    adapter.close()
    with pytest.raises(SimulationStepError):
        adapter.step()
    with pytest.raises(SimulationStepError):
        adapter.set_signal_state("dev-tls-1", "G")


# -- adapter contract and configuration -----------------------------------


def test_fake_adapter_rejects_unknown_vehicle_and_signal() -> None:
    adapter = FakeSimulationAdapter()
    adapter.start(scenario())
    adapter.step()
    with pytest.raises(SimulationVehicleError):
        adapter.get_vehicle_state("unknown-vehicle")
    with pytest.raises(SimulationSignalError):
        adapter.get_signal_state("unknown-signal")
    with pytest.raises(SimulationSignalError):
        adapter.set_signal_state("unknown-signal", "G")
    adapter.close()


def test_fake_adapter_rejects_unmapped_network_and_dynamic_flows() -> None:
    adapter = FakeSimulationAdapter()
    with pytest.raises(SimulationConfigurationError):
        adapter.start(scenario().model_copy(update={"network_id": "some-other-network"}))
    with pytest.raises(SimulationConfigurationError):
        adapter.start(
            scenario().model_copy(
                update={
                    "traffic_flows": [
                        TrafficFlow(
                            demand_id="flow",
                            edge_ids=["dev-edge-0"],
                            vehicle_count=1,
                            depart_period_seconds=1.0,
                        )
                    ]
                }
            )
        )
    with pytest.raises(SimulationConfigurationError):
        adapter.start(
            scenario().model_copy(update={"route_edge_ids": ["invented-edge"]})
        )
    with pytest.raises(SimulationConfigurationError):
        adapter.start(
            scenario().model_copy(
                update={"traffic_signals": [signal_definition(signal_id="dev-tls-9")]}
            )
        )


def test_every_adapter_satisfies_the_replaceable_contract() -> None:
    assert isinstance(FakeSimulationAdapter(), SimulationAdapter)
    assert isinstance(SumoTraCIAdapter(Settings()), SimulationAdapter)
    for error in (
        SimulationVehicleError("x"),
        SimulationSignalError("x"),
        SimulationTimeoutError("x"),
        SimulationTerminatedError("x"),
        SimulationStepError("x"),
        SimulationConfigurationError("x"),
        SimulationUnavailableError("x"),
    ):
        assert isinstance(error.code, str) and error.code


# -- SUMO adapter without a SUMO installation -----------------------------


def test_sumo_adapter_reports_unavailable_without_configuration() -> None:
    with pytest.raises(SimulationUnavailableError) as error:
        SumoTraCIAdapter(Settings(sumo_config_path=None)).start(scenario())
    assert error.value.code == "SIMULATOR_UNAVAILABLE"


def test_sumo_adapter_reports_unavailable_without_a_config_file(tmp_path) -> None:
    settings = Settings(
        sumo_config_path=str(tmp_path / "absent.sumocfg"), sumo_network_id="team-net"
    )
    with pytest.raises(SimulationUnavailableError):
        SumoTraCIAdapter(settings).start(scenario().model_copy(update={"network_id": "team-net"}))


def test_sumo_adapter_rejects_a_network_it_was_not_configured_for() -> None:
    settings = Settings(sumo_config_path="scenario.sumocfg", sumo_network_id="team-net")
    with pytest.raises(SimulationUnavailableError):
        SumoTraCIAdapter(settings).start(scenario())


def test_sumo_adapter_refuses_use_before_start_and_after_close() -> None:
    adapter = SumoTraCIAdapter(Settings())
    with pytest.raises(SimulationConnectionError):
        adapter.step()
    with pytest.raises(SimulationConnectionError):
        adapter.get_vehicle_state("sentinel-vehicle")
    with pytest.raises(SimulationConnectionError):
        adapter.get_signal_state("junction-1")
    with pytest.raises(SimulationConnectionError):
        adapter.set_signal_state("junction-1", "1")
    assert adapter.simulation_time_seconds() == 0
    adapter.close()
    adapter.close()
    with pytest.raises(SimulationConnectionError):
        adapter.is_finished()


# -- U/V/W. comparison maths ---------------------------------------------


def test_comparison_reports_neutral_result_without_inventing_an_improvement() -> None:
    metrics = SimulationMetrics(
        emergency_vehicle_travel_time_seconds=10.0,
        stopped_time_seconds=2.0,
        number_of_stops=2,
        route_completed=True,
    )
    comparison = build_comparison_metrics(metrics, metrics)
    assert comparison.travel_time_delta_seconds == 0
    assert comparison.travel_time_improvement_percent == 0
    assert comparison.stopped_time_delta_seconds == 0
    assert comparison.stops_delta == 0


def test_comparison_reports_a_measured_improvement() -> None:
    comparison = build_comparison_metrics(
        SimulationMetrics(emergency_vehicle_travel_time_seconds=10.0),
        SimulationMetrics(emergency_vehicle_travel_time_seconds=8.0),
    )
    assert comparison.travel_time_delta_seconds == -2
    assert comparison.travel_time_improvement_percent == pytest.approx(20)


def test_comparison_reports_a_measured_degradation() -> None:
    comparison = build_comparison_metrics(
        SimulationMetrics(emergency_vehicle_travel_time_seconds=10.0, number_of_stops=1),
        SimulationMetrics(emergency_vehicle_travel_time_seconds=13.0, number_of_stops=4),
    )
    assert comparison.travel_time_delta_seconds == 3
    assert comparison.travel_time_improvement_percent == pytest.approx(-30)
    assert comparison.stops_delta == 3


def test_comparison_handles_zero_denominator_and_missing_values() -> None:
    assert (
        build_comparison_metrics(
            SimulationMetrics(emergency_vehicle_travel_time_seconds=0.0),
            SimulationMetrics(emergency_vehicle_travel_time_seconds=0.0),
        ).travel_time_improvement_percent
        is None
    )
    missing = build_comparison_metrics(
        SimulationMetrics(emergency_vehicle_travel_time_seconds=10.0, number_of_stops=1),
        SimulationMetrics(),
    )
    assert missing.travel_time_delta_seconds is None
    assert missing.stops_delta is None
    assert build_comparison_metrics(None, None).travel_time_delta_seconds is None
    assert (
        build_comparison_metrics(
            SimulationMetrics(emergency_vehicle_travel_time_seconds=5.0), None
        ).travel_time_improvement_percent
        is None
    )


def test_metric_overrides_are_only_a_test_hook() -> None:
    injected = SimulationMetrics(emergency_vehicle_travel_time_seconds=99.0)
    adapter = FakeSimulationAdapter(metric_overrides=injected)
    adapter.start(scenario())
    assert adapter.get_metrics(scenario().emergency_vehicle_id) == injected
    assert FakeSimulationAdapter().metric_overrides is None


# -- live phase verification --------------------------------------------

# The verified clearpath_demo J1 program. Controlled links 0-2 are the north_in
# approaches, so only phase 2 clears the emergency corridor.
DEMO_PHASE_STATES = ["rrrGggGGg", "rrryyyyyy", "GGGrrrrrr", "yyyrrrrrr"]
DEMO_LINK_IN_LANES = [
    "north_in_0", "north_in_0", "north_in_0",
    "east_in_0", "east_in_0", "east_in_0",
    "west_in_0", "west_in_0", "west_in_0",
]


def demo_signal(**updates: object) -> SignalDefinition:
    base: dict[str, object] = {
        "traffic_signal_id": SIGNAL_UUID,
        "signal_id": "J1",
        "edge_id": "north_in",
        "valid_phases": ["0", "1", "2", "3"],
        "preemption_phase": "2",
        "release_phase": "0",
        "safe_transitions": {"0": ["2"], "1": ["0"], "2": ["0"], "3": ["0"]},
        "maximum_duration_seconds": 20,
        "initial_phase": "0",
    }
    return SignalDefinition(**{**base, **updates})


def test_edge_link_indices_selects_only_the_mapped_approach() -> None:
    assert edge_link_indices(DEMO_LINK_IN_LANES, "north_in") == [0, 1, 2]
    assert edge_link_indices(DEMO_LINK_IN_LANES, "east_in") == [3, 4, 5]
    assert edge_link_indices(DEMO_LINK_IN_LANES, "west_in") == [6, 7, 8]
    assert edge_link_indices(DEMO_LINK_IN_LANES, "south_out") == []


def test_verified_green_preemption_phase_is_accepted() -> None:
    verify_preemption_phase_grants_green(
        demo_signal(), DEMO_PHASE_STATES, DEMO_LINK_IN_LANES
    )


@pytest.mark.parametrize(
    ("phase", "indication"),
    [
        ("1", "r"),  # rrryyyyyy -> north_in red
        ("3", "y"),  # yyyrrrrrr -> north_in yellow
    ],
)
def test_a_phase_that_does_not_clear_the_corridor_is_refused(phase, indication) -> None:
    """A yellow-only or red phase is not a successful pre-emption.

    Phases 1 and 3 are both valid, non-release phases, so the metadata itself is
    accepted and it is the live phase program that refuses them.
    """
    signal = demo_signal(
        preemption_phase=phase,
        safe_transitions={"0": [phase], phase: ["0"], "2": ["0"]},
    )
    with pytest.raises(SimulationConfigurationError) as error:
        verify_preemption_phase_grants_green(
            signal, DEMO_PHASE_STATES, DEMO_LINK_IN_LANES
        )
    assert error.value.code == "SIMULATION_CONFIGURATION_INVALID"
    assert f"shows {indication} for edge 'north_in'" in error.value.args[0]
    assert "must show green" in error.value.args[0]


def test_preemption_phase_outside_the_declared_program_is_refused() -> None:
    """Metadata may declare a phase the live SUMO program does not contain."""
    signal = demo_signal(
        valid_phases=["0", "1", "2", "3", "7"],
        preemption_phase="7",
        safe_transitions={"0": ["7"], "7": ["0"], "2": ["0"]},
    )
    with pytest.raises(SimulationConfigurationError) as error:
        verify_preemption_phase_grants_green(
            signal, DEMO_PHASE_STATES, DEMO_LINK_IN_LANES
        )
    assert error.value.code == "SIMULATION_CONFIGURATION_INVALID"
    assert "has no phase 7 in the SUMO program" in error.value.args[0]


def test_preemption_phase_equal_to_release_phase_is_still_invalid_metadata() -> None:
    """The schema invariant is untouched by live phase verification."""
    with pytest.raises(ValueError, match="must differ"):
        demo_signal(preemption_phase="0", release_phase="0")


def test_signal_controlling_no_mapped_approach_is_refused() -> None:
    with pytest.raises(SimulationConfigurationError) as error:
        verify_preemption_phase_grants_green(
            demo_signal(edge_id="south_out"),
            DEMO_PHASE_STATES,
            DEMO_LINK_IN_LANES,
        )
    assert "controls no approach" in error.value.args[0]


def test_green_phase_for_another_approach_is_not_accepted_for_this_corridor() -> None:
    """Phase 2 is green for north_in but red for west_in."""
    signal = demo_signal(edge_id="west_in")
    with pytest.raises(SimulationConfigurationError):
        verify_preemption_phase_grants_green(
            signal, DEMO_PHASE_STATES, DEMO_LINK_IN_LANES
        )
