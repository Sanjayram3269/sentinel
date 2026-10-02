"""Optional real-SUMO smoke test.

This module never fabricates a result. It is skipped unless an actual SUMO
installation *and* an explicit network mapping are supplied through the
environment, and it reports only values SUMO itself produced.

Enable it with a teammate-provided network::

    SENTINEL_SUMO_SMOKE=1
    SUMO_BINARY=sumo
    SUMO_CONFIG_PATH=/abs/path/scenario.sumocfg
    SUMO_NETWORK_ID=team-network-v1
    SENTINEL_SUMO_SMOKE_EDGES=edge-a,edge-b
    SENTINEL_SUMO_SMOKE_VEHICLE_TYPE=emergency
    SENTINEL_SUMO_SMOKE_SIGNALS='[{"traffic_signal_id":"<uuid>","signal_id":"junction-1",
        "edge_id":"edge-b","valid_phases":["0","1","2"],
        "initial_phase":"0","preemption_phase":"1","release_phase":"0",
        "maximum_duration_seconds":20,
        "safe_transitions":{"0":["1"],"1":["0"],"2":["0"]}}]'

``SENTINEL_SUMO_SMOKE_SIGNALS`` may also be omitted when
``SUMO_CONFIG_PATH`` points inside this repository: the committed
``simulation/scenarios/clearpath_demo/signals.json`` is then used, so the
verified demo network runs from a clean clone with no hand-written JSON.

``preemption_phase`` is verified against the live SUMO phase program. A phase
that does not show green for the mapped corridor edge is refused with
``SIMULATION_CONFIGURATION_INVALID`` rather than simulated.

The default test suite never requires any of these variables.
"""

import json
import os
from pathlib import Path
from uuid import UUID

import pytest

from app.config import get_settings
from app.schemas.simulation import (
    SignalDefinition,
    SimulationMetrics,
    SimulationMode,
    SimulationScenario,
)
from app.services.simulation.adapter import (
    SimulationAdapterError,
    adapter_metadata,
)
from app.services.simulation.runner import SimulationRunner
from app.services.simulation.service import (
    _improvement_percent,
    build_comparison_metrics,
)
from app.services.simulation.sumo_adapter import SumoTraCIAdapter

MISSION_UUID = UUID(int=21)
VEHICLE_UUID = UUID(int=22)
ROUTE_UUID = UUID(int=23)

BACKEND_DIR = Path(__file__).resolve().parents[2]

REQUIRED_ENV = (
    "SENTINEL_SUMO_SMOKE",
    "SUMO_BINARY",
    "SUMO_CONFIG_PATH",
    "SUMO_NETWORK_ID",
    "SENTINEL_SUMO_SMOKE_EDGES",
)


@pytest.fixture(scope="module")
def sumo_settings():
    missing = [name for name in REQUIRED_ENV if not os.getenv(name)]
    if missing:
        pytest.skip(f"real SUMO smoke test not configured; missing {', '.join(missing)}")
    if os.getenv("SENTINEL_SUMO_SMOKE") != "1":
        pytest.skip("set SENTINEL_SUMO_SMOKE=1 to run the real SUMO smoke test")
    get_settings.cache_clear()
    settings = get_settings()
    if not Path(settings.sumo_config_path or "").is_file():
        pytest.skip("configured SUMO_CONFIG_PATH is not a readable file")
    try:
        import traci  # noqa: F401
    except ImportError:
        pytest.skip("TraCI module is not importable in this environment")
    yield settings
    get_settings.cache_clear()


REPO_SIGNAL_METADATA = BACKEND_DIR / "simulation" / "scenarios" / "clearpath_demo" / "signals.json"


def _repo_signal_metadata() -> str:
    """Use the committed demo metadata when the config is the repo demo.

    This keeps a clean clone runnable without retyping the verified J1 phase
    metadata, while an explicit environment variable always wins.
    """
    config_path = (get_settings().sumo_config_path or "").replace("\\", "/")
    if "clearpath_demo" not in config_path or not REPO_SIGNAL_METADATA.is_file():
        return "[]"
    return REPO_SIGNAL_METADATA.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def smoke_scenario(sumo_settings) -> SimulationScenario:
    raw = os.getenv("SENTINEL_SUMO_SMOKE_SIGNALS") or _repo_signal_metadata()
    try:
        payload = json.loads(raw)
        # The committed file wraps its definitions in an object; the environment
        # variable is a bare list. Accept either shape.
        items = payload["signals"] if isinstance(payload, dict) else payload
        definitions = [SignalDefinition.model_validate(item) for item in items]
    except (ValueError, TypeError, KeyError) as error:
        pytest.skip(f"signal metadata is not valid: {error}")
    return SimulationScenario(
        mission_id=MISSION_UUID,
        vehicle_id=VEHICLE_UUID,
        route_id=ROUTE_UUID,
        mode=SimulationMode.CLEARPATH,
        network_id=sumo_settings.sumo_network_id or "",
        route_edge_ids=[
            edge.strip()
            for edge in os.environ["SENTINEL_SUMO_SMOKE_EDGES"].split(",")
            if edge.strip()
        ],
        traffic_signals=definitions,
        traffic_flows=[],
        emergency_vehicle_id=f"sentinel-{VEHICLE_UUID}",
        emergency_vehicle_configuration={
            "sumo_vehicle_type_id": os.getenv(
                "SENTINEL_SUMO_SMOKE_VEHICLE_TYPE", "emergency"
            )
        },
        seed=20261002,
        max_simulation_seconds=int(os.getenv("SENTINEL_SUMO_SMOKE_HORIZON", "300")),
        start_time="2026-10-02T00:00:00Z",
        correlation_id=UUID(int=24),
    )


def test_real_sumo_baseline_completes_and_reports_measured_metrics(
    sumo_settings, smoke_scenario
) -> None:
    scenario = smoke_scenario.model_copy(update={"mode": SimulationMode.BASELINE})
    result = SimulationRunner().execute(scenario, SumoTraCIAdapter(sumo_settings))

    assert result.actions == []
    metrics = result.metrics
    assert isinstance(metrics, SimulationMetrics)
    assert metrics.route_completed is True
    assert metrics.emergency_vehicle_travel_time_seconds is not None
    assert metrics.emergency_vehicle_travel_time_seconds > 0


def test_real_sumo_clearpath_reports_measured_actions_or_an_explicit_error(
    sumo_settings, smoke_scenario
) -> None:
    adapter = SumoTraCIAdapter(sumo_settings)
    try:
        result = SimulationRunner().execute(smoke_scenario, adapter)
    except SimulationAdapterError as error:
        # A rejection is a valid, explicit outcome; it must never be reported as
        # an improvement and never silently converted into a success.
        pytest.skip(f"SUMO rejected the smoke scenario explicitly: {error}")
    # Without signal metadata this loop would pass vacuously, which would hide a
    # missing or malformed signals file. Fail loudly instead.
    assert smoke_scenario.traffic_signals, (
        "no signal metadata was loaded; set SENTINEL_SUMO_SMOKE_SIGNALS or point "
        "SUMO_CONFIG_PATH at the committed clearpath_demo network"
    )
    assert result.actions, "configured corridor signals produced no decision"
    for action in result.actions:
        assert action.reason_code
        assert action.explanation
        # Every recorded pre-emption must have been released before teardown.
        assert action.released_at_seconds is not None
        assert action.action.requested_phase == smoke_scenario.traffic_signals[
            0
        ].preemption_phase


def test_real_sumo_baseline_and_clearpath_are_measured_not_asserted(
    sumo_settings, smoke_scenario
) -> None:
    """Both modes are run against the same live network and only measured.

    This asserts provenance, not a performance outcome: the numbers must be
    non-null, must come from a SUMO process, and the comparison must be whatever
    SUMO actually produced. A CLEARPATH run that is slower than its baseline is
    a valid result and must not fail this test.
    """
    baseline_scenario = smoke_scenario.model_copy(update={"mode": SimulationMode.BASELINE})
    baseline = SimulationRunner().execute(baseline_scenario, SumoTraCIAdapter(sumo_settings))
    clearpath = SimulationRunner().execute(smoke_scenario, SumoTraCIAdapter(sumo_settings))

    for metrics in (baseline.metrics, clearpath.metrics):
        assert metrics.route_completed is True
        assert metrics.emergency_vehicle_travel_time_seconds is not None
        assert metrics.simulation_duration_seconds is not None

    # BASELINE must never request a signal action.
    assert baseline.actions == []
    # Every recorded CLEARPATH action must be released and fully explained.
    for action in clearpath.actions:
        assert action.released_at_seconds is not None
        assert action.explanation

    # At least one signal is configured, so CLEARPATH must have decided one.
    assert clearpath.actions, "configured corridor signals produced no decision"

    comparison = build_comparison_metrics(baseline.metrics, clearpath.metrics)
    # The comparison is a pure function of the two measured runs.
    expected = _improvement_percent(
        baseline.metrics.emergency_vehicle_travel_time_seconds,
        clearpath.metrics.emergency_vehicle_travel_time_seconds,
    )
    assert comparison.travel_time_improvement_percent == expected
    if comparison.travel_time_improvement_percent is None:
        pytest.skip("travel time was not measurable in both runs")


def test_real_sumo_adapter_reports_live_sumo_provenance(
    sumo_settings, smoke_scenario
) -> None:
    """Metrics must be traceable to an actual SUMO process, not to a fixture."""
    adapter = SumoTraCIAdapter(sumo_settings)
    result = SimulationRunner().execute(
        smoke_scenario.model_copy(update={"mode": SimulationMode.BASELINE}), adapter
    )
    metadata = adapter_metadata(adapter)

    assert metadata["sumo_build"].startswith("SUMO "), metadata
    assert metadata["sumo_binary"], metadata
    assert metadata["sumo_seed"] == smoke_scenario.seed
    assert result.metrics.route_completed is True
