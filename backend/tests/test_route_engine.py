"""Deterministic routing provider, scoring, and resilience tests."""

from uuid import UUID

import pytest

from app.models.enums import ResilienceRole
from app.schemas.domain import GeoPoint
from app.schemas.routing import RouteProposal
from app.services.route_resilience import (
    assign_route_roles,
    route_diversity,
    route_overlap_ratio,
)
from app.services.route_scoring import (
    PredictionSignals,
    RouteScoreWeights,
    RouteScoringEngine,
    ScoredRoute,
)
from app.services.routing.baseline import BaselineDevelopmentProvider
from app.services.routing.base import RoutingRequest

WEIGHTS = RouteScoreWeights(0.25, 0.15, 0.15, 0.20, 0.10, 0.15)
ORIGIN = GeoPoint(latitude=0, longitude=0)
DESTINATION = GeoPoint(latitude=1, longitude=1)


def proposal(
    *,
    name: str,
    intermediate: GeoPoint,
    duration: int = 100,
    distance: float = 1000,
    risk: float | None = None,
    failure: float | None = None,
    congestion: float | None = None,
    hazard: float | None = None,
    road_ids: list[str] | None = None,
) -> RouteProposal:
    return RouteProposal(
        name=name,
        geometry=[ORIGIN, intermediate, DESTINATION],
        distance_meters=distance,
        estimated_duration_seconds=duration,
        risk_score=risk,
        predicted_failure_probability=failure,
        congestion_score=congestion,
        hazard_exposure=hazard,
        road_segment_ids=road_ids or [],
    )


def scorer(**settings: float) -> RouteScoringEngine:
    return RouteScoringEngine(
        WEIGHTS,
        failure_threshold=settings.get("failure_threshold", 0.7),
        hazard_threshold=settings.get("hazard_threshold", 0.8),
    )


def scored(
    route_id: int,
    route_proposal: RouteProposal,
    score: float,
    *,
    viable: bool = True,
    metrics: dict[str, float] | None = None,
        provider_order: int = 0,
) -> ScoredRoute:
    return ScoredRoute(
        candidate_id=UUID(int=route_id),
        proposal=route_proposal,
        score=score,
        score_coverage=1.0,
        viable=viable,
        rejection_reasons=(),
        metrics=metrics or {},
            provider_order=provider_order,
    )


def route_set() -> list[tuple[UUID, RouteProposal]]:
    return [
        (
            UUID(int=1),
            proposal(
                name="north",
                intermediate=GeoPoint(latitude=0.7, longitude=0.3),
                duration=100,
            ),
        ),
        (
            UUID(int=2),
            proposal(
                name="south",
                intermediate=GeoPoint(latitude=0.3, longitude=0.7),
                duration=120,
            ),
        ),
    ]


def test_development_provider_normalizes_only_supplied_candidates() -> None:
    request = RoutingRequest(
        mission_id=UUID(int=10),
        vehicle_id=UUID(int=11),
        origin=ORIGIN,
        destination=DESTINATION,
        proposals=(route_set()[0][1],),
        parameters={"profile": "emergency"},
    )
    result = BaselineDevelopmentProvider().calculate_routes(request)
    assert len(result) == 1
    assert result[0] == request.proposals[0]
    assert BaselineDevelopmentProvider.name == "baseline_development_provider"


@pytest.mark.parametrize(
    ("field", "first", "second"),
    [
        ("estimated_duration_seconds", 100, 200),
        ("distance_meters", 1000, 2000),
        ("risk_score", 0.1, 0.9),
        ("predicted_failure_probability", 0.1, 0.9),
        ("congestion_score", 0.1, 0.9),
        ("hazard_exposure", 0.1, 0.9),
    ],
)
def test_available_metric_influences_route_score(
    field: str, first: float, second: float
) -> None:
    values = route_set()
    left = values[0][1].model_copy(update={field: first})
    right = values[1][1].model_copy(update={field: second})
    result = scorer().score([(values[0][0], left), (values[1][0], right)])
    assert result[0].score < result[1].score


def test_task4_predictions_are_consumed_as_score_features() -> None:
    candidates = route_set()
    prediction_weighted = RouteScoringEngine(
        RouteScoreWeights(0.05, 0.05, 0.05, 0.75, 0.05, 0.05),
        failure_threshold=0.7,
        hazard_threshold=0.8,
    ).score(
        candidates,
        {
            UUID(int=1): PredictionSignals(failure_probability=0.9),
            UUID(int=2): PredictionSignals(failure_probability=0.1),
        },
    )
    assert prediction_weighted[0].score > prediction_weighted[1].score


def test_missing_metrics_are_reweighted_and_coverage_is_reported() -> None:
    candidate_id, route_proposal = route_set()[0]
    result = scorer().score([(candidate_id, route_proposal)])[0]
    assert result.score_coverage == pytest.approx(0.4)
    assert 0 <= result.score <= 1


def test_equal_metrics_and_single_candidate_do_not_divide_by_zero() -> None:
    one_id, one_proposal = route_set()[0]
    two_id, two_proposal = route_set()[1]
    two_proposal = two_proposal.model_copy(
        update={"estimated_duration_seconds": one_proposal.estimated_duration_seconds,
                "distance_meters": one_proposal.distance_meters}
    )
    one_score = scorer().score([(one_id, one_proposal)])[0]
    pair = scorer().score([(one_id, one_proposal), (two_id, two_proposal)])
    assert one_score.score == pytest.approx(pair[0].score)
    assert all(0 <= candidate.score <= 1 for candidate in pair)


def test_failure_and_hazard_thresholds_mark_route_nonviable() -> None:
    candidate_id, route_proposal = route_set()[0]
    result = scorer().score(
        [
            (
                candidate_id,
                route_proposal.model_copy(
                    update={"predicted_failure_probability": 0.8, "hazard_exposure": 0.9}
                ),
            )
        ]
    )[0]
    assert not result.viable
    assert result.rejection_reasons == (
        "failure_probability_exceeds_threshold",
        "hazard_exposure_exceeds_threshold",
    )


def test_provider_declared_unreachable_route_is_nonviable() -> None:
    candidate_id, route_proposal = route_set()[0]
    result = scorer().score(
        [(candidate_id, route_proposal.model_copy(update={"reachable": False}))]
    )[0]
    assert result.viable is False
    assert result.rejection_reasons == ("destination_unreachable",)


def test_one_route_has_low_resilience_and_deterministic_score() -> None:
    candidate_id, route_proposal = route_set()[0]
    item = scored(1, route_proposal, 0.1, metrics={"failure": 0.1})
    first = assign_route_roles([item], minimum_diversity=0.3)
    second = assign_route_roles([item], minimum_diversity=0.3)
    assert first.roles == {candidate_id: ResilienceRole.PRIMARY}
    assert first.primary_id == item.candidate_id
    assert first.backup_id is None
    assert first.level.value == "LOW_RESILIENCE"
    assert first.score == second.score


def test_primary_backup_and_contingency_require_geometric_diversity() -> None:
    candidates = [
        scored(route_id, route_proposal, score, metrics={"failure": 0.1})
        for (candidate_id, route_proposal), route_id, score in zip(
            route_set()
            + [
                (
                    UUID(int=3),
                    proposal(
                        name="west",
                        intermediate=GeoPoint(latitude=0.8, longitude=0.8),
                        duration=140,
                    ),
                )
            ],
            (1, 2, 3),
            (0.1, 0.2, 0.3),
            strict=True,
        )
    ]
    result = assign_route_roles(candidates, minimum_diversity=0.3)
    assert result.primary_id == UUID(int=1)
    assert result.backup_id == UUID(int=2)
    assert result.contingency_id == UUID(int=3)
    assert result.level.value == "HIGH_RESILIENCE"


def test_exactly_overlapping_routes_have_no_independent_backup() -> None:
    route_proposal = route_set()[0][1]
    first = scored(1, route_proposal, 0.1)
    second = scored(2, route_proposal.model_copy(update={"name": "same geometry"}), 0.2)
    assert route_overlap_ratio(first, second) == pytest.approx(1.0)
    assert route_diversity(first, second) == pytest.approx(0.0)
    result = assign_route_roles([first, second], minimum_diversity=0.3)
    assert result.backup_id is None
    assert result.level.value == "LOW_RESILIENCE"


def test_provider_road_segment_ids_drive_overlap_when_available() -> None:
    first_proposal = route_set()[0][1].model_copy(
        update={"road_segment_ids": ["A", "SHARED"]}
    )
    second_proposal = route_set()[1][1].model_copy(
        update={"road_segment_ids": ["SHARED", "B"]}
    )
    first = scored(1, first_proposal, 0.1)
    second = scored(2, second_proposal, 0.2)
    assert route_overlap_ratio(first, second) == pytest.approx(0.5)
    assert route_diversity(first, second) == pytest.approx(0.5)


def test_failed_backup_is_excluded_and_roles_are_stable() -> None:
    first_id, first_proposal = route_set()[0]
    second_id, second_proposal = route_set()[1]
    candidates = [
        scored(1, first_proposal, 0.1, metrics={"failure": 0.1}),
        scored(2, second_proposal, 0.2, viable=False, metrics={"failure": 1.0}),
    ]
    first = assign_route_roles(candidates, minimum_diversity=0.3)
    second = assign_route_roles(candidates, minimum_diversity=0.3)
    assert first.roles == second.roles
    assert first.primary_id == UUID(int=1)
    assert first.backup_id is None


def test_equal_score_roles_follow_provider_order_not_random_ids() -> None:
    first_proposal, second_proposal = route_set()[0][1], route_set()[1][1]
    candidates = [
        scored(999, first_proposal, 0.5, provider_order=0),
        scored(1, second_proposal, 0.5, provider_order=1),
    ]
    result = assign_route_roles(candidates, minimum_diversity=0.3)
    assert result.primary_id == UUID(int=999)
    assert result.backup_id == UUID(int=1)