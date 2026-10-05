"""Fast, database-free coverage for the Phase 7 mission-plan bridge.

These tests pin the parts that must be right for the real SUMO run to mean
anything: the canonical ``RoadEdge.id`` -> SUMO translation, the ordering
guarantee, and the rule that an unmapped edge fails closed instead of quietly
producing a measurement for a different route.

End-to-end behaviour over PostGIS lives in
``tests/integration/test_plan_clearpath_validation.py`` (fake adapter) and
``tests/integration/test_plan_clearpath_sumo.py`` (real SUMO).
"""

from __future__ import annotations

import pytest

from app.schemas.simulation import (
    SafetyDecision,
    SignalActionResult,
    SimulationComparisonMetrics,
    SimulationMetrics,
    SimulationMode,
)
from app.schemas.simulation_evidence import EvidenceStatus, SafetyStatus
from app.services.simulation.mission_plan import (
    PlanSimulationError,
    _evidence_status,
    _safety_status,
    translate_route_to_sumo_edges,
)
from app.schemas.simulation_evidence import PlanSimulationCode


# ======================================================================
# A/B/C: MissionPlan -> SUMO edge mapping, ordering, fail-closed
# ======================================================================


def test_translation_maps_canonical_ids_to_sumo_edges() -> None:
    mapping = {"edge-a": "north_in", "edge-b": "south_out"}
    assert translate_route_to_sumo_edges(["edge-a", "edge-b"], mapping) == [
        "north_in",
        "south_out",
    ]


def test_translation_preserves_route_order() -> None:
    """Order is traffic-relevant: reversing it would simulate a different trip."""
    mapping = {"a": "E0", "b": "E1", "c": "E2"}
    forward = translate_route_to_sumo_edges(["a", "b", "c"], mapping)
    reversed_ = translate_route_to_sumo_edges(["c", "b", "a"], mapping)
    assert forward == ["E0", "E1", "E2"]
    assert reversed_ == ["E2", "E1", "E0"]
    assert forward != reversed_


def test_translation_fails_closed_on_an_unmapped_edge() -> None:
    with pytest.raises(PlanSimulationError) as caught:
        translate_route_to_sumo_edges(["a", "missing"], {"a": "E0"})
    assert caught.value.code is PlanSimulationCode.MISSING_SUMO_EDGE_MAPPING
    assert caught.value.status_code == 422
    assert "missing" in caught.value.detail["message"]


def test_translation_rejects_an_empty_route() -> None:
    with pytest.raises(PlanSimulationError) as caught:
        translate_route_to_sumo_edges([], {})
    assert caught.value.code is PlanSimulationCode.INVALID_ROUTE


def test_translation_does_not_drop_unmapped_edges_to_succeed() -> None:
    """A partial mapping must not yield a shorter, still-'successful' route."""
    with pytest.raises(PlanSimulationError):
        translate_route_to_sumo_edges(["a", "b", "c"], {"a": "E0", "c": "E2"})


@pytest.mark.parametrize(
    "code,expected_status",
    [
        (PlanSimulationCode.MISSION_NOT_FOUND, 404),
        (PlanSimulationCode.PLAN_NOT_FOUND, 404),
        (PlanSimulationCode.PLAN_MISSION_MISMATCH, 404),
        (PlanSimulationCode.ROUTE_NOT_FOUND, 404),
        (PlanSimulationCode.ROUTE_NOT_ACTIVE, 409),
        (PlanSimulationCode.INVALID_ROUTE, 422),
        (PlanSimulationCode.MISSING_SUMO_EDGE_MAPPING, 422),
        (PlanSimulationCode.ROUTE_NETWORK_MISMATCH, 422),
        (PlanSimulationCode.SUMO_UNAVAILABLE, 503),
    ],
)
def test_failure_codes_map_to_meaningful_statuses(code, expected_status) -> None:
    """A meaningful failure must never surface as a generic 500."""
    error = PlanSimulationError(code, "detail")
    assert error.status_code == expected_status
    assert error.detail["code"] == code.value


# ======================================================================
# L/M: comparison mathematics and unavailable metrics
# ======================================================================


def _metrics(**overrides) -> SimulationMetrics:
    base = {
        "emergency_vehicle_travel_time_seconds": 56.0,
        "stopped_time_seconds": 33.0,
        "number_of_stops": 1,
        "average_speed_meters_per_second": 5.0,
        "route_completed": True,
    }
    base.update(overrides)
    return SimulationMetrics(**base)


class _Run:
    def __init__(self, status, metrics, actions=()):
        self.status = status
        self.metrics = metrics
        self.clearpath_actions = list(actions)


def test_evidence_status_distinguishes_failure_modes() -> None:
    from app.models.enums import SimulationStatus

    complete = SimulationStatus.COMPLETED
    failed = SimulationStatus.FAILED
    assert (
        _evidence_status(_Run(complete, _metrics()), _Run(complete, _metrics()))
        is EvidenceStatus.COMPARABLE
    )
    assert (
        _evidence_status(_Run(failed, None), _Run(complete, _metrics()))
        is EvidenceStatus.BASELINE_FAILED
    )
    assert (
        _evidence_status(_Run(complete, _metrics()), _Run(failed, None))
        is EvidenceStatus.CLEARPATH_FAILED
    )
    # Completed but unmeasured: not a crash, just not comparable.
    assert (
        _evidence_status(_Run(complete, None), _Run(complete, _metrics()))
        is EvidenceStatus.COMPARISON_UNAVAILABLE
    )


def test_safety_status_reports_no_action_and_all_rejected_distinctly() -> None:
    assert _safety_status(0, 0) is SafetyStatus.NO_ACTION_PROPOSED
    assert _safety_status(0, 3) is SafetyStatus.REJECTED_SIMULATION_ONLY
    assert _safety_status(2, 3) is SafetyStatus.APPROVED_SIMULATION_ONLY


# ======================================================================
# I: CLEARPATH remains simulation-only
# ======================================================================


def test_evidence_never_authorises_real_world_control() -> None:
    """The scope notice must stay attached to every evidence object."""
    from app.schemas.simulation_evidence import SimulationEvidenceRead

    assert SimulationEvidenceRead.model_fields["simulation_only"].default is True
    assert "No real-world traffic signal is controlled" in (
        SimulationEvidenceRead.model_fields["scope_notice"].default
    )
