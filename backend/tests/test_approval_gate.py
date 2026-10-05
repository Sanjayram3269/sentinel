"""Fast, DB-free tests for the Phase 8 authorization boundary.

These exercise the pure decision logic: what the gate refuses, what an approval
binds to, and the arithmetic of measured evidence. They deliberately do not need
PostgreSQL, so the safety rules can be checked in milliseconds.
"""

from __future__ import annotations

import pytest

from app.services.approval import (
    REVIEWER_IDENTITY_KIND,
    ApprovalCode,
    ApprovalError,
    ExecutionDecision,
)
from app.services.approval_review import _delta, _improvement
from app.services.plan_modification import _apply_to_payload
from app.schemas.approval import (
    ApprovalRequest,
    ModifyPlanRequest,
    PlanModification,
    RejectionRequest,
)


# ======================================================================
# Failure codes and HTTP mapping
# ======================================================================


def test_every_failure_code_maps_to_a_meaningful_status():
    from app.services.approval import _FAILURE_STATUS

    assert set(_FAILURE_STATUS) == set(ApprovalCode)
    # Nothing may collapse into a generic 500.
    assert set(_FAILURE_STATUS.values()) <= {404, 409, 422}
    assert ApprovalCode.PLAN_VERSION_MISMATCH.value in _FAILURE_STATUS
    assert ApprovalCode.STALE_NETWORK.value in _FAILURE_STATUS


def test_approval_error_carries_its_own_code():
    error = ApprovalError(ApprovalCode.STALE_NETWORK, "network changed")
    assert error.status_code == 409
    assert error.detail["code"] == "STALE_NETWORK"
    assert error.detail["detail"] == "network changed"


def test_reviewer_identity_is_labelled_as_a_prototype():
    # The project has no authentication system. The identity is recorded
    # explicitly as a development string rather than pretending to be a
    # principal.
    assert "development" in REVIEWER_IDENTITY_KIND
    assert "prototype" in REVIEWER_IDENTITY_KIND


# ======================================================================
# L/M: measured-evidence arithmetic
# ======================================================================


@pytest.mark.parametrize(
    ("baseline", "clearpath", "expected"),
    [(56.0, 19.0, 66.07142857142857), (10.0, 10.0, 0.0), (10.0, 12.0, -20.0)],
)
def test_improvement_percent_is_computed_from_measurements(baseline, clearpath, expected):
    assert _improvement(baseline, clearpath) == pytest.approx(expected)


@pytest.mark.parametrize("baseline", [0.0, -5.0])
def test_zero_or_negative_baseline_yields_no_percentage(baseline):
    # A zero baseline has no meaningful percentage. Reporting 0% or infinity
    # would be a fabricated measurement.
    assert _improvement(baseline, 1.0) is None


def test_missing_measurement_yields_no_percentage():
    assert _improvement(None, 19.0) is None
    assert _improvement(56.0, None) is None


def test_delta_is_none_when_either_side_is_missing():
    assert _delta(56.0, 19.0) == -37.0
    assert _delta(None, 19.0) is None
    assert _delta(56.0, None) is None


# ======================================================================
# Request contracts
# ======================================================================


def test_approval_request_requires_the_version_being_decided():
    request = ApprovalRequest(plan_version=4, reviewer_id="dispatcher-1")
    assert request.plan_version == 4
    with pytest.raises(ValueError):
        ApprovalRequest(reviewer_id="dispatcher-1")


def test_rejection_requires_a_reason():
    with pytest.raises(ValueError):
        RejectionRequest(plan_version=1, reviewer_id="d", comment="")


def test_resource_modification_requires_a_list():
    with pytest.raises(ValueError):
        PlanModification(field="RESOURCE", reason="swap")
    ok = PlanModification(field="RESOURCE", value_list=[], reason="clear resources")
    assert ok.value_list == []


def test_only_resources_may_carry_a_list():
    with pytest.raises(ValueError):
        PlanModification(field="ROUTE", value_list=[], reason="nope")


def test_modification_rejects_duplicate_fields():
    with pytest.raises(ValueError):
        ModifyPlanRequest(
            plan_version=1,
            reviewer_id="d",
            modifications=[
                PlanModification(field="ROUTE", reason="a"),
                PlanModification(field="ROUTE", reason="b"),
            ],
        )


# ======================================================================
# Modification payload
# ======================================================================


def test_modification_changes_only_the_selected_route():
    payload = {
        "selected": {"route_id": "old", "hospital_id": "h1", "resource_ids": []},
        "score": {"value": 0.5, "coverage": 0.7},
    }
    updated, applied = _apply_to_payload(
        payload, [PlanModification(field="ROUTE", value=None, reason="manual")]
    )
    assert updated["selected"]["route_id"] is None
    assert updated["selected"]["hospital_id"] == "h1"
    # The optimizer's own conclusions are not silently recomputed or dropped.
    assert updated["score"] == {"value": 0.5, "coverage": 0.7}
    assert updated["reviewer_modification"]["applied_at"]
    assert applied[0]["field"] == "ROUTE"


def test_modification_does_not_mutate_the_original_payload():
    payload = {"selected": {"route_id": "old"}, "score": {"value": 0.5}}
    _apply_to_payload(payload, [PlanModification(field="ROUTE", reason="x")])
    assert payload["selected"]["route_id"] == "old"
    assert "reviewer_modification" not in payload


def test_modification_flags_a_stale_carried_score():
    payload = {"selected": {}, "score": {"value": 0.5}}
    updated, _ = _apply_to_payload(
        payload, [PlanModification(field="HOSPITAL", reason="closer trauma bay")]
    )
    # A reviewer who changed the selection invalidated the old score; the new
    # version must say so rather than inherit a number that no longer applies.
    assert "no longer describes" in updated["reviewer_modification"]["score_note"]


def test_resource_modification_replaces_the_whole_assignment():
    from uuid import uuid4

    ids = [uuid4(), uuid4()]
    payload = {"selected": {"resource_ids": ["old"]}}
    updated, _ = _apply_to_payload(
        payload, [PlanModification(field="RESOURCE", value_list=ids, reason="swap")]
    )
    assert updated["selected"]["resource_ids"] == [str(item) for item in ids]


# ======================================================================
# Execution boundary
# ======================================================================


def test_execution_mode_has_exactly_one_value():
    from app.schemas.approval import ExecutionMode

    # There is no REAL or LIVE mode. That is the point.
    assert [item.value for item in ExecutionMode] == ["PROTOTYPE"]


def test_execution_record_states_the_prototype_boundary():
    from app.services.mission_execution import NO_REAL_ACTUATOR_NOTICE

    assert "PROTOTYPE" in NO_REAL_ACTUATOR_NOTICE
    assert "No real vehicle" in NO_REAL_ACTUATOR_NOTICE


def test_replan_result_can_never_be_auto_approved():
    from app.services.mission_execution import ReplanResult

    result = ReplanResult(replan_requested=True, reason="ROUTE_DEVIATION")
    assert result.auto_approved is False
    assert result.requires_human_approval is True


def test_no_real_world_actuator_client_is_imported():
    """The safety boundary, asserted rather than documented.

    An HTTP client that could reach a traffic-signal or dispatch API would make
    every "simulation-only" claim in this project false.
    """
    import ast
    import pathlib

    offenders: list[str] = []
    for path in pathlib.Path("app").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = getattr(func, "attr", "") or getattr(func, "id", "")
            if name in {"urlopen", "urlretrieve"} or name.startswith("http"):
                offenders.append(f"{path}:{node.lineno}:{name}")
    assert offenders == [], offenders


def test_gate_decision_is_not_authorized_by_default():
    from app.services.approval import GateDecision

    decision = GateDecision(decision=ExecutionDecision.NOT_AUTHORIZED)
    assert decision.authorized is False
    assert decision.as_payload()["decision"] == "NOT_AUTHORIZED"
