"""Unit coverage for Phase 6 hospital, resource, and optimizer logic.

Tests 1-18 of the phase brief. Everything here is pure: no database, no
PostGIS, no network. That is deliberate -- the constraint layer, the allocator
and the option scorer are the parts where a subtle bug would silently produce a
wrong dispatch, and they should be testable exhaustively in milliseconds.

The end-to-end behaviour over the real ``osm_urban_v1`` network lives in
``tests/integration/test_mission_optimization.py``.
"""

from __future__ import annotations

import pytest

from dataclasses import replace

from app.config import Settings
from app.schemas.domain import GeoPoint
from app.services.route_resilience import route_overlap_ratio
from app.services.route_scoring import ScoredRoute
from app.services.optimization.constraints import (
    HospitalProfile,
    ResourceProfile,
    Rejection,
    RouteProfile,
    declared_capabilities,
    evaluate_hospital,
    evaluate_resource,
    evaluate_route,
    match_capabilities,
)
from app.services.optimization.hospital_suitability import UNAVAILABLE, HospitalSuitabilityRanker
from app.services.optimization.mission_optimizer import decide_plan
from app.services.optimization.resource_allocation import ResourceAllocator

BASE_LON = 77.59
BASE_LAT = 13.09


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "road_network_key": "unit_network",
        "optimization_hospital_match_meters": 250.0,
        "optimization_max_hospitals": 8,
        "optimization_max_route_candidates": 8,
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def _hospital(
    name: str = "Fixture General",
    *,
    latitude: float = BASE_LAT,
    longitude: float = BASE_LON,
    capacity_available: int = 4,
    capacity_total: int = 10,
    capabilities: tuple[str, ...] = ("trauma", "emergency"),
    reliability: float | None = 0.90,
    operational_status: str = "OPERATIONAL",
) -> HospitalProfile:
    """A development fixture hospital. Never real data; see module docstring."""
    return HospitalProfile(
        hospital_id=_id(name),
        name=name,
        latitude=latitude,
        longitude=longitude,
        capacity_total=capacity_total,
        capacity_available=capacity_available,
        operational_status=operational_status,
        declared_capabilities=capabilities,
        emergency_capable=True,
        reliability=reliability,
    )


def _id(seed: str):
    from uuid import NAMESPACE_URL, uuid5

    return uuid5(NAMESPACE_URL, f"unit/{seed}")


def _route(
    seed: str,
    *,
    latitude: float = BASE_LAT,
    longitude: float = BASE_LON,
    eta_seconds: int = 300,
    role: str | None = "PRIMARY",
    rank: int = 1,
    status: str = "CANDIDATE",
    viable: bool = True,
    risk: float | None = 0.10,
) -> RouteProfile:
    return RouteProfile(
        candidate_id=_id(f"cand/{seed}"),
        route_id=_id(f"route/{seed}"),
        vehicle_id=_id("vehicle/1"),
        planning_cycle_id=_id("cycle/1"),
        status=status,
        route_rank=rank,
        estimated_duration_seconds=eta_seconds,
        distance_meters=2000.0,
        route_score_viable=viable,
        resilience_role=role,
        destination_latitude=latitude,
        destination_longitude=longitude,
        origin_latitude=BASE_LAT,
        origin_longitude=BASE_LON,
        geometry_points=42,
        risk_score=risk,
        predicted_failure_probability=risk,
    )


def _vehicle(
    call_sign: str,
    *,
    vehicle_type: str = "AMBULANCE",
    status: str = "AVAILABLE",
    mission_id=None,
    latitude: float | None = BASE_LAT,
    longitude: float | None = BASE_LON,
    capabilities: tuple[str, ...] = (),
) -> ResourceProfile:
    return ResourceProfile(
        vehicle_id=_id(f"veh/{call_sign}"),
        call_sign=call_sign,
        vehicle_type=vehicle_type,
        status=status,
        mission_id=mission_id,
        declared_capabilities=capabilities,
        latitude=latitude,
        longitude=longitude,
    )


def _rank(hospitals, routes, *, required_capabilities=(), settings=None, response=None):
    return HospitalSuitabilityRanker(settings or _settings()).rank(
        hospitals,
        routes,
        required_capabilities=required_capabilities,
        origin_position=(BASE_LON, BASE_LAT),
        resource_response_meters=response,
    )


def _allocate(vehicles, *, types=(), capabilities=()):
    return ResourceAllocator(_id("mission/1")).allocate(
        vehicles,
        required_vehicle_types=types,
        required_capabilities=capabilities,
        incident_position=(BASE_LON, BASE_LAT),
    )


# ======================================================================
# HOSPITAL 1-6
# ======================================================================


def test_01_hospital_capability_match_is_accepted() -> None:
    verdict, match = evaluate_hospital(_hospital(capabilities=("trauma", "cardiac")), ["trauma"])
    assert verdict.accepted
    assert match.matched == ("trauma",)
    assert match.missing == ()


def test_02_hospital_capability_mismatch_is_rejected() -> None:
    """A hospital that declares capabilities but not this one is rejected."""
    verdict, match = evaluate_hospital(_hospital(capabilities=("cardiac",)), ["trauma"])
    assert not verdict.accepted
    assert Rejection.HOSPITAL_CAPABILITY_MISMATCH in verdict.rejections
    assert match.missing == ("trauma",)


def test_02b_hospital_without_declared_capabilities_cannot_be_assumed_suitable() -> None:
    """Silence is not consent: unverifiable is rejected, not treated as a pass."""
    verdict, match = evaluate_hospital(_hospital(capabilities=()), ["trauma"])
    assert not verdict.accepted
    assert Rejection.HOSPITAL_CAPABILITY_UNKNOWN in verdict.rejections
    assert match.subject_declares is False


def test_03_hospital_capacity_available_is_accepted_and_zero_is_rejected() -> None:
    assert evaluate_hospital(_hospital(capacity_available=3))[0].accepted
    verdict, _ = evaluate_hospital(_hospital(capacity_available=0))
    assert not verdict.accepted
    assert Rejection.HOSPITAL_CAPACITY_UNAVAILABLE in verdict.rejections


def test_03b_closed_hospital_is_rejected() -> None:
    verdict, _ = evaluate_hospital(_hospital(operational_status="CLOSED"))
    assert not verdict.accepted
    assert Rejection.HOSPITAL_NOT_OPERATIONAL in verdict.rejections


def test_04_hospital_without_any_viable_route_is_rejected() -> None:
    """Capability and capacity are not enough; nothing can reach it."""
    routing = _rank([_hospital()], [_route("a", latitude=13.5, longitude=77.9)])
    assert routing.options == ()
    assert any(
        Rejection.HOSPITAL_NO_VIABLE_ROUTE.value in entry.rejections
        for entry in routing.rejected
    )


def test_05_hospital_ranking_is_not_nearest_first() -> None:
    """A farther, better-equipped hospital can outrank the nearest one.

    Proven in both directions, because "not nearest-only" must not become
    "always ignore ETA": under ETA-led weights the near hospital wins, and
    under capacity/reliability-led weights the far hospital wins. The decision
    moves with the stated objective, which is the property being claimed.
    """
    near = _hospital("Near", capacity_available=1, capacity_total=10, reliability=0.70)
    far = _hospital(
        "Far",
        latitude=BASE_LAT,
        longitude=BASE_LON + 0.02,
        capacity_available=20,
        capacity_total=24,
        reliability=0.99,
    )
    routes = [
        _route("near", eta_seconds=120, longitude=BASE_LON),
        _route("far", eta_seconds=900, longitude=BASE_LON + 0.02),
    ]
    eta_led = _settings(
        optimization_weight_eta=0.55,
        optimization_weight_capacity=0.10,
        optimization_weight_reliability=0.10,
        optimization_weight_capability=0.15,
        optimization_weight_route_risk=0.10,
        optimization_weight_resource_proximity=0.00,
    )
    capacity_led = _settings(
        optimization_weight_eta=0.20,
        optimization_weight_capacity=0.30,
        optimization_weight_reliability=0.30,
        optimization_weight_capability=0.10,
        optimization_weight_route_risk=0.10,
        optimization_weight_resource_proximity=0.00,
    )
    assert _rank([near, far], routes, settings=eta_led).accepted[0].hospital.name == "Near"
    capacity_first = _rank([near, far], routes, settings=capacity_led)
    assert capacity_first.accepted[0].hospital.name == "Far"
    # The losing hospital is still ranked, not discarded.
    assert len(capacity_first.accepted) == 2


def test_06_hospital_ranking_is_explainable_and_says_unavailable() -> None:
    """Every factor is present, and a missing one is named, not invented."""
    hospital = _hospital(reliability=None)
    ranking = _rank([hospital], [_route("a")])
    block = ranking.accepted[0].explanation()
    assert block["capability_match"] is False  # no requirement stated, nothing matched
    assert block["required_capabilities_declared"] is False
    assert block["capacity_available_to_mission"] is True
    assert block["reliability"] == UNAVAILABLE
    assert block["best_eta_seconds"] == 300
    assert block["viable_route_count"] == 1
    assert block["capability_declared_by_hospital"] is True


def test_06b_rejected_hospital_explanation_names_the_reason() -> None:
    ranking = _rank(
        [_hospital(capabilities=("cardiac",))], [_route("a")], required_capabilities=["trauma"]
    )
    assert ranking.accepted == ()
    block = ranking.rejected_explanations()[0]
    assert block["rejected_by"] == "hard_constraint"
    assert block["capability_missing"] == ["trauma"]
    assert block["rejections"] == [Rejection.HOSPITAL_CAPABILITY_MISMATCH.value]


def test_06c_capability_reader_accepts_every_stored_key_spelling() -> None:
    assert declared_capabilities({"capabilities": ["Trauma"], "specialties": ["CARDIAC"]}) == (
        "cardiac",
        "trauma",
    )
    assert declared_capabilities(None) == ()
    match = match_capabilities(["trauma"], (), requirements_declared=True)
    assert match.satisfied is False


# ======================================================================
# RESOURCES 7-11
# ======================================================================


def test_07_available_resource_is_selected() -> None:
    allocation = _allocate([_vehicle("AMB-01")], types=["AMBULANCE"])
    assert allocation.feasible
    assert [item.resource.call_sign for item in allocation.selections] == ["AMB-01"]
    assert allocation.unmet == ()


def test_08_unavailable_resource_is_never_selected() -> None:
    for status in ("EN_ROUTE", "AT_SCENE", "TRANSPORTING", "OFFLINE"):
        allocation = _allocate([_vehicle("AMB-01", status=status)], types=["AMBULANCE"])
        assert not allocation.feasible, status
        assert allocation.selections == ()
        assert allocation.unmet[0].requirement == "vehicle_type:AMBULANCE"


def test_09_incompatible_resource_type_is_rejected() -> None:
    allocation = _allocate([_vehicle("FIRE-01", vehicle_type="FIRE_TRUCK")], types=["AMBULANCE"])
    assert not allocation.feasible
    assert "FIRE-01" in allocation.unmet[0].considered


def test_09b_resource_committed_to_another_mission_is_rejected() -> None:
    """A vehicle on someone else's active mission is never taken."""
    allocation = _allocate(
        [_vehicle("AMB-01", mission_id=_id("mission/other"))], types=["AMBULANCE"]
    )
    assert not allocation.feasible
    assert allocation.selections == ()


def test_10_duplicate_assignment_is_prevented() -> None:
    """One vehicle cannot satisfy two requirement slots."""
    allocation = _allocate([_vehicle("AMB-01")], types=["AMBULANCE", "RESCUE"])
    assert not allocation.feasible
    assert [item.resource.call_sign for item in allocation.selections] == ["AMB-01"]
    assert [item.requirement for item in allocation.unmet] == ["vehicle_type:RESCUE"]


def test_10b_requirements_do_not_silently_downgrade() -> None:
    """Asking for an ambulance when only a fire truck exists is a failure."""
    allocation = _allocate(
        [_vehicle("FIRE-01", vehicle_type="FIRE_TRUCK")], types=["AMBULANCE"]
    )
    assert not allocation.feasible
    assert allocation.selections == ()


def test_11_infeasible_resource_requirement_is_explicit() -> None:
    allocation = _allocate([], types=["AMBULANCE"])
    assert not allocation.feasible
    assert allocation.unmet[0].reason == Rejection.REQUIREMENT_UNSATISFIED.value


def test_11b_absence_of_requirements_allocates_nothing_and_says_so() -> None:
    """The mission model has no clinical requirement field; none is invented."""
    allocation = _allocate([_vehicle("AMB-01")])
    assert allocation.feasible
    assert allocation.selections == ()
    assert allocation.requirements_declared is False
    assert "required_vehicle_types" in allocation.missing


def test_11b_resource_selection_is_deterministic_under_input_reordering() -> None:
    vehicles = [
        _vehicle("AMB-03", longitude=BASE_LON + 0.01),
        _vehicle("AMB-01", longitude=BASE_LON),
        _vehicle("AMB-02", longitude=BASE_LON + 0.005),
    ]
    first = _allocate(vehicles, types=["AMBULANCE"])
    second = _allocate(list(reversed(vehicles)), types=["AMBULANCE"])
    assert [item.resource.call_sign for item in first.selections] == ["AMB-01"]
    assert [item.resource.call_sign for item in second.selections] == ["AMB-01"]


def test_11c_resource_response_distance_is_unavailable_when_position_unknown() -> None:
    allocation = _allocate([_vehicle("AMB-01", latitude=None, longitude=None)], types=["AMBULANCE"])
    assert allocation.feasible
    assert allocation.selections[0].response_meters is None
    assert allocation.response_meters is None


# ======================================================================
# OPTIMIZER 12-18
# ======================================================================


def test_12_feasible_mission_plan() -> None:
    ranking = _rank([_hospital()], [_route("a")])
    allocation = _allocate([_vehicle("AMB-01")], types=["AMBULANCE"])
    decision = decide_plan(ranking, allocation)
    assert decision.feasible
    assert decision.blocking == ()
    assert decision.option is not None
    assert decision.option.hospital.name == "Fixture General"
    assert decision.option.factors.eta_seconds == 300
    assert decision.option.score.total is not None


def test_13_infeasible_mission_plan_states_why() -> None:
    """No hospital, no resources, and no combination all report explicitly."""
    no_hospital = _rank([], [_route("a")])
    assert decide_plan(no_hospital, _allocate([], types=[])).blocking == (
        "no_hospital_passed_hard_constraints",
        "no_hospital_and_route_combination_available",
    )
    ranking = _rank([_hospital()], [_route("a")])
    allocation = _allocate([], types=["AMBULANCE"])
    decision = decide_plan(ranking, allocation)
    assert not decision.feasible
    assert decision.blocking == ("resource_requirement_unmet:vehicle_type:AMBULANCE",)
    assert decision.option is None


def test_14_route_and_hospital_are_selected_jointly() -> None:
    """The chosen pair must be the best *pair*, not the best hospital and the
    best route independently. Hospital A has the faster route; Hospital B has
    the better overall cost, and B must win with its own route."""
    settings = _settings(
        optimization_weight_eta=0.20,
        optimization_weight_capacity=0.30,
        optimization_weight_reliability=0.30,
        optimization_weight_capability=0.10,
        optimization_weight_route_risk=0.10,
        optimization_weight_resource_proximity=0.00,
    )
    hospital_a = _hospital("A", capacity_available=1, capacity_total=10, reliability=0.70)
    hospital_b = _hospital(
        "B", longitude=BASE_LON + 0.01, capacity_available=15, capacity_total=18, reliability=0.98
    )
    ranking = _rank(
        [hospital_a, hospital_b],
        [
            _route("a", eta_seconds=100, longitude=BASE_LON),
            _route("b", eta_seconds=800, longitude=BASE_LON + 0.01),
        ],
        settings=settings,
    )
    decision = decide_plan(ranking, _allocate([_vehicle("AMB-01")], types=["AMBULANCE"]))
    assert decision.feasible
    assert decision.option.hospital.name == "B"
    # B is only ever paired with B's own route.
    assert decision.option.route.route_id == _id("route/b")


def test_14b_each_hospital_is_paired_only_with_routes_that_reach_it() -> None:
    hospital = _hospital(longitude=BASE_LON + 0.5)  # far from every candidate destination
    ranking = _rank([hospital], [_route("a", longitude=BASE_LON)])
    assert ranking.options == ()


def test_15_resource_hospital_and_route_appear_together_in_one_decision() -> None:
    ranking = _rank([_hospital()], [_route("a")], response=742.0)
    allocation = _allocate(
        [_vehicle("AMB-09", longitude=BASE_LON + 0.004), _vehicle("AMB-01")],
        types=["AMBULANCE"],
    )
    decision = decide_plan(ranking, allocation)
    option = decision.option
    assert option is not None
    # One decision carries all three parts.
    assert option.hospital.hospital_id is not None
    assert option.route.route_id is not None
    assert allocation.selected_ids == (_id("veh/AMB-01"),)
    assert option.factors.resource_response_meters == 742.0
    assert option.score.costs.resource_proximity is not None


def test_16_primary_route_is_selected_when_viable() -> None:
    """Equal-cost candidates prefer PRIMARY, matching Phase 5's role order."""
    ranking = _rank(
        [_hospital(capacity_available=5)],
        [
            _route("backup", eta_seconds=300, role="BACKUP", rank=2),
            _route("primary", eta_seconds=300, role="PRIMARY", rank=1),
        ],
    )
    decision = decide_plan(ranking, _allocate([]))
    assert decision.feasible
    assert decision.option.route.resilience_role == "PRIMARY"


def test_17_backup_is_selected_when_primary_is_infeasible() -> None:
    """A FAILED primary is a hard rejection, and the backup takes over."""
    ranking = _rank(
        [_hospital()],
        [
            _route("primary", eta_seconds=120, role="PRIMARY", status="FAILED"),
            _route("backup", eta_seconds=600, role="BACKUP", rank=2),
            _route("contingency", eta_seconds=900, role="CONTINGENCY", rank=3),
        ],
    )
    decision = decide_plan(ranking, _allocate([]))
    assert decision.feasible
    assert decision.option.route.resilience_role == "BACKUP"
    rejected = {str(item.candidate_id): item for item in ranking.route_rejections}
    assert str(_id("cand/primary")) in rejected
    assert rejected[str(_id("cand/primary"))].reasons == (
        Rejection.ROUTE_STATUS_NON_VIABLE.value,
    )


def test_17b_route_rejected_by_phase5_scorer_is_a_hard_rejection() -> None:
    """``backup_viable`` is the Phase 5 verdict; the optimizer respects it."""
    ranking = _rank(
        [_hospital()],
        [
            _route("risky", eta_seconds=60, role="PRIMARY", viable=False),
            _route("safe", eta_seconds=600, role="BACKUP", rank=2),
        ],
    )
    decision = decide_plan(ranking, _allocate([]))
    assert decision.feasible
    assert decision.option.route.resilience_role == "BACKUP"


def test_17c_all_routes_infeasible_makes_the_plan_infeasible() -> None:
    ranking = _rank([_hospital()], [_route("primary", role="PRIMARY", viable=False)])
    decision = decide_plan(ranking, _allocate([]))
    assert not decision.feasible
    assert "no_hospital_and_route_combination_available" in decision.blocking


def test_18_repeated_execution_is_deterministic() -> None:
    """Same inputs, same plan -- across repeated calls and shuffled input."""
    hospitals = [
        _hospital("A", longitude=BASE_LON, capacity_available=3, capacity_total=10),
        _hospital("B", longitude=BASE_LON + 0.01, capacity_available=9, capacity_total=12),
        _hospital("C", longitude=BASE_LON + 0.02, capacity_available=5, capacity_total=8),
    ]
    routes = [
        _route("a", longitude=BASE_LON, eta_seconds=200),
        _route("b", longitude=BASE_LON + 0.01, eta_seconds=400),
        _route("c", longitude=BASE_LON + 0.02, eta_seconds=300),
        _route("b2", longitude=BASE_LON + 0.01, eta_seconds=450, role="BACKUP", rank=2),
    ]
    vehicles = [_vehicle("AMB-01"), _vehicle("AMB-02", longitude=BASE_LON + 0.01)]

    def run() -> tuple:
        ranking = _rank(hospitals, routes)
        allocation = _allocate(vehicles, types=["AMBULANCE"])
        decision = decide_plan(ranking, allocation)
        assert decision.option is not None
        return (
            decision.option.hospital.hospital_id,
            decision.option.route.route_id,
            decision.option.score.total,
            [entry.hospital.hospital_id for entry in ranking.accepted],
            allocation.selected_ids,
        )

    first = run()
    assert first == run() == run()

    # Input order must not matter: the ranker sorts by total keys internally,
    # so a database returning rows in a different order yields the same plan.
    def run_shuffled() -> tuple:
        ranking = _rank(list(reversed(hospitals)), list(reversed(routes)))
        allocation = _allocate(list(reversed(vehicles)), types=["AMBULANCE"])
        decision = decide_plan(ranking, allocation)
        assert decision.option is not None
        return (
            decision.option.hospital.hospital_id,
            decision.option.route.route_id,
            decision.option.score.total,
            [entry.hospital.hospital_id for entry in ranking.accepted],
            allocation.selected_ids,
        )

    assert run_shuffled() == first


DEFAULT_WEIGHTS = {
    "eta": 0.30,
    "route_risk": 0.20,
    "capability": 0.20,
    "capacity": 0.10,
    "reliability": 0.10,
    "resource_proximity": 0.10,
}


@pytest.mark.parametrize("weight", sorted(DEFAULT_WEIGHTS))
def test_18b_objective_weights_actually_change_the_objective(weight: str) -> None:
    """Zeroing a weight must remove exactly that weight's share of the score.

    Coverage is ``available_weight / total_weight``. In this single-option
    scenario every factor except ``resource_proximity`` is computable, so
    zeroing one weight must drop coverage by precisely its default value. That
    is a real assertion about the weighting; checking only that coverage is
    below 1.0 would pass for any configuration at all.
    """
    hospitals, routes = [_hospital()], [_route("a")]

    baseline = HospitalSuitabilityRanker(_settings()).rank(
        hospitals, routes, origin_position=(BASE_LON, BASE_LAT)
    )
    adjusted = HospitalSuitabilityRanker(
        _settings(**{f"optimization_weight_{weight}": 0.0})
    ).rank(hospitals, routes, origin_position=(BASE_LON, BASE_LAT))

    # Coverage is available_weight / total_weight, so zeroing a weight shrinks
    # numerator and denominator together -- the change is not linear in the
    # weight. The exact post-change coverage is therefore recomputed here.
    total_weight = sum(DEFAULT_WEIGHTS.values())
    missing_weight = DEFAULT_WEIGHTS["resource_proximity"]
    available_weight = total_weight - missing_weight
    zeroed = DEFAULT_WEIGHTS[weight]
    expected = (
        1.0
        if weight == "resource_proximity"
        else (available_weight - zeroed) / (total_weight - zeroed)
    )

    assert baseline.options[0].score.coverage == pytest.approx(available_weight / total_weight)
    assert adjusted.options[0].score.coverage == pytest.approx(expected)
    # The weight is genuinely consumed: coverage moved, and the factor that was
    # zeroed contributes nothing even though it is computable.
    assert adjusted.options[0].score.coverage != pytest.approx(
        baseline.options[0].score.coverage
    )
    assert adjusted.options[0].score.contributions.get(weight, 0.0) == pytest.approx(0.0)


def test_18b2_unavailable_factor_is_excluded_and_named() -> None:
    """The factor with no data is named and leaves the denominator."""
    ranking = HospitalSuitabilityRanker(_settings()).rank(
        [_hospital()], [_route("a")], origin_position=(BASE_LON, BASE_LAT)
    )
    score = ranking.options[0].score
    # resource_response_meters was not supplied, so that factor is unavailable.
    assert score.unavailable_factors == ("resource_proximity",)
    assert "resource_proximity" not in score.contributions
    assert score.coverage == pytest.approx(0.90)

    supplied = HospitalSuitabilityRanker(_settings()).rank(
        [_hospital()],
        [_route("a")],
        origin_position=(BASE_LON, BASE_LAT),
        resource_response_meters=500.0,
    )
    assert supplied.options[0].score.unavailable_factors == ()
    assert supplied.options[0].score.coverage == pytest.approx(1.0)


def test_18c_all_weights_zero_is_rejected_by_settings() -> None:
    with pytest.raises(ValueError, match="optimization weight"):
        _settings(
            optimization_weight_eta=0.0,
            optimization_weight_route_risk=0.0,
            optimization_weight_capability=0.0,
            optimization_weight_capacity=0.0,
            optimization_weight_reliability=0.0,
            optimization_weight_resource_proximity=0.0,
        )


def test_resource_capability_requirement_is_a_hard_constraint() -> None:
    allocation = _allocate(
        [_vehicle("AMB-01", capabilities=("basic_life_support",))],
        capabilities=["advanced_airway"],
    )
    assert not allocation.feasible
    assert allocation.unmet[0].requirement == "capability:advanced_airway"


# ======================================================================
# Route diversity reported in the plan explanation (brief section 10)
# ======================================================================


def _scored(seed: str, segment_ids: list[str]) -> ScoredRoute:
    """A ScoredRoute carrying only what route_overlap_ratio reads."""
    from app.schemas.routing import RouteProposal

    proposal = RouteProposal(
        name=f"route-{seed}",
        geometry=[
            GeoPoint(latitude=BASE_LAT, longitude=BASE_LON),
            GeoPoint(latitude=BASE_LAT + 0.001, longitude=BASE_LON + 0.001),
        ],
        distance_meters=100.0,
        estimated_duration_seconds=100,
        road_segment_ids=segment_ids,
    )
    return ScoredRoute(
        candidate_id=_id(f"cand/{seed}"),
        proposal=proposal,
        score=0.5,
        score_coverage=1.0,
        viable=True,
        rejection_reasons=(),
        metrics={},
    )


def test_diversity_matches_the_phase5_resilience_definition() -> None:
    """The plan's diversity must equal what the resilience engine computes.

    If the two ever disagreed, a plan could report a route as well-separated
    while the resilience engine had appointed its backup on a different
    measure. This pins them to the same formula.
    """
    from app.services.optimization.mission_optimizer import _diversity_from_primary

    primary = _route("primary", role="PRIMARY", eta_seconds=100)
    primary = replace(primary, road_segment_ids=("a", "b", "c", "d"))
    backup = _route("backup", role="BACKUP", rank=2, eta_seconds=300)
    backup = replace(backup, road_segment_ids=("c", "d", "e", "f", "g"))
    candidates = [primary, backup]

    reported = _diversity_from_primary(backup, candidates)
    expected = 1.0 - route_overlap_ratio(_scored("p", ["a", "b", "c", "d"]),
                                         _scored("b", ["c", "d", "e", "f", "g"]))
    assert reported == pytest.approx(expected)
    assert reported == pytest.approx(1.0 - 2 / 4)


def test_diversity_is_unavailable_when_it_cannot_be_computed() -> None:
    """The primary itself, and edge-less routes, report None -- not 0.0."""
    from app.services.optimization.mission_optimizer import _diversity_from_primary

    primary = _route("primary", role="PRIMARY")
    primary = replace(primary, road_segment_ids=("a", "b"))
    assert _diversity_from_primary(primary, [primary]) is None

    no_edges = _route("backup", role="BACKUP", rank=2)
    assert _diversity_from_primary(no_edges, [primary]) is None

    # No primary in the candidate set at all.
    orphan = replace(_route("backup", role="BACKUP", rank=2), road_segment_ids=("a",))
    assert _diversity_from_primary(orphan, [orphan]) is None


def test_identical_routes_report_zero_diversity() -> None:
    """Full overlap is a real measurement of zero, distinct from "unavailable"."""
    from app.services.optimization.mission_optimizer import _diversity_from_primary

    primary = replace(_route("primary", role="PRIMARY"), road_segment_ids=("a", "b", "c"))
    clone = replace(_route("clone", role="BACKUP", rank=2), road_segment_ids=("a", "b", "c"))
    assert _diversity_from_primary(clone, [primary, clone]) == pytest.approx(0.0)
