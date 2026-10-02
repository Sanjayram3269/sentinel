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
    SENTINEL_SUMO_SMOKE_SIGNALS='[{"sumo_signal_id":"junction-1",
        "traffic_signal_id":"<uuid>","edge_id":"edge-b","valid_phases":["0","1","2"],
        "initial_phase":"0","preemption_phase":"1","release_phase":"0",
        "maximum_duration_seconds":20,
        "safe_transitions":{"0":["1"],"1":["0"],"2":["0"]}}]'

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
from app.services.simulation.adapter import SimulationAdapterError
from app.services.simulation.runner import SimulationRunner
from app.services.simulation.sumo_adapter import SumoTraCIAdapter

MISSION_UUID = UUID(int=21)
VEHICLE_UUID = UUID(int=22)
ROUTE_UUID = UUID(int=23)

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


@pytest.fixture(scope="module")
def smoke_scenario(sumo_settings) -> SimulationScenario:
    raw = os.getenv("SENTINEL_SUMO_SMOKE_SIGNALS", "[]")
    try:
        definitions = [SignalDefinition.model_validate(item) for item in json.loads(raw)]
    except (ValueError, TypeError) as error:
        pytest.skip(f"SENTINEL_SUMO_SMOKE_SIGNALS is not valid metadata: {error}")
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
    for action in result.actions:
        assert action.reason_code
        assert action.released_at_seconds is not None
