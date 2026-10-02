"""Deterministic route role assignment and geographic diversity analysis."""

from dataclasses import dataclass
from math import asin, cos, radians, sin, sqrt
from uuid import UUID

from app.models.enums import ResilienceRole, RouteStatus
from app.schemas.routing import ResilienceLevel
from app.services.route_scoring import ScoredRoute

EARTH_RADIUS_METERS = 6_371_008.8
NON_VIABLE_ROUTE_STATUSES = frozenset(
    {RouteStatus.FAILED, RouteStatus.ABORTED, RouteStatus.DEGRADED}
)


def is_route_status_viable(status: RouteStatus) -> bool:
    """Only healthy/candidate/active routes may fill resilience roles."""
    return status not in NON_VIABLE_ROUTE_STATUSES


@dataclass(frozen=True)
class ResilienceResult:
    roles: dict[UUID, ResilienceRole]
    diversity: float
    failure_exposure: float | None
    score: float
    level: ResilienceLevel
    primary_id: UUID | None
    backup_id: UUID | None
    contingency_id: UUID | None
    explanation: tuple[str, ...]


def route_overlap_ratio(first: ScoredRoute, second: ScoredRoute) -> float:
    """Approximate shared line length from exact shared segments or provider IDs."""
    first_ids = set(first.proposal.road_segment_ids)
    second_ids = set(second.proposal.road_segment_ids)
    if first_ids and second_ids:
        return len(first_ids & second_ids) / min(len(first_ids), len(second_ids))

    first_segments = _segments(first)
    second_segments = _segments(second)
    first_length = sum(length for _, length in first_segments)
    second_length = sum(length for _, length in second_segments)
    denominator = min(first_length, second_length)
    if denominator <= 0:
        return 1.0
    second_segment_lengths = dict(second_segments)
    shared = sum(
        min(length, second_segment_lengths[segment])
        for segment, length in first_segments
        if segment in second_segment_lengths
    )
    return min(1.0, shared / denominator)


def route_diversity(first: ScoredRoute, second: ScoredRoute) -> float:
    return max(0.0, min(1.0, 1.0 - route_overlap_ratio(first, second)))


def assign_route_roles(
    candidates: list[ScoredRoute], *, minimum_diversity: float
) -> ResilienceResult:
    viable = sorted(
        (candidate for candidate in candidates if candidate.viable),
        key=lambda candidate: (candidate.score, candidate.provider_order),
    )
    roles: dict[UUID, ResilienceRole] = {}
    primary = viable[0] if viable else None
    if primary is not None:
        roles[primary.candidate_id] = ResilienceRole.PRIMARY

    remaining = [candidate for candidate in viable if candidate is not primary]
    backup = None
    if primary is not None:
        backup = next(
            (
                candidate
                for candidate in remaining
                if route_diversity(primary, candidate) >= minimum_diversity
            ),
            None,
        )
    if backup is not None:
        roles[backup.candidate_id] = ResilienceRole.BACKUP

    contingency = None
    if primary is not None and backup is not None:
        contingency = next(
            (
                candidate
                for candidate in remaining
                if candidate is not backup
                and route_diversity(primary, candidate) >= minimum_diversity
                and route_diversity(backup, candidate) >= minimum_diversity
            ),
            None,
        )
    if contingency is not None:
        roles[contingency.candidate_id] = ResilienceRole.CONTINGENCY

    assigned = [candidate for candidate in (primary, backup, contingency) if candidate]
    pairwise_diversity = [
        route_diversity(assigned[left], assigned[right])
        for left in range(len(assigned))
        for right in range(left + 1, len(assigned))
    ]
    diversity = sum(pairwise_diversity) / len(pairwise_diversity) if pairwise_diversity else 0.0
    failure_values = [
        candidate.metrics.get("failure")
        if candidate.metrics.get("failure") is not None
        else candidate.metrics.get("risk")
        for candidate in assigned
    ]
    known_failures = [value for value in failure_values if value is not None]
    failure_exposure = max(known_failures) if known_failures else None
    failure_component = 1.0 - failure_exposure if failure_exposure is not None else 0.5

    score = (
        (0.2 if primary else 0.0)
        + (0.2 if backup else 0.0)
        + (0.1 if contingency else 0.0)
        + 0.3 * diversity
        + 0.2 * failure_component
    )
    if primary is None:
        level = ResilienceLevel.NONE
    elif backup is None:
        level = ResilienceLevel.LOW
    elif contingency is None or score < 0.75:
        level = ResilienceLevel.MEDIUM
    else:
        level = ResilienceLevel.HIGH

    explanation = []
    if primary:
        explanation.append("Primary is the lowest-scoring viable candidate")
    else:
        explanation.append("No viable route candidate is available")
    if backup:
        explanation.append("Backup meets the configured geometric diversity threshold")
    else:
        explanation.append("No sufficiently diverse viable backup route is available")
    if contingency:
        explanation.append("Contingency is sufficiently diverse from primary and backup")
    if failure_exposure is None:
        explanation.append("Failure exposure is unknown; resilience score uses a neutral penalty")
    else:
        explanation.append("Failure exposure uses available failure probability or route risk")

    return ResilienceResult(
        roles=roles,
        diversity=diversity,
        failure_exposure=failure_exposure,
        score=max(0.0, min(1.0, score)),
        level=level,
        primary_id=primary.candidate_id if primary else None,
        backup_id=backup.candidate_id if backup else None,
        contingency_id=contingency.candidate_id if contingency else None,
        explanation=tuple(explanation),
    )


def _segments(candidate: ScoredRoute) -> list[tuple[tuple[tuple[float, float], tuple[float, float]], float]]:
    points = [
        (point.longitude, point.latitude) for point in candidate.proposal.geometry
    ]
    output = []
    for start, end in zip(points, points[1:]):
        forward = (start, end)
        reverse = (end, start)
        key = min(forward, reverse)
        output.append((key, _haversine(start, end)))
    return output


def _haversine(first: tuple[float, float], second: tuple[float, float]) -> float:
    longitude_a, latitude_a = first
    longitude_b, latitude_b = second
    latitude_delta = radians(latitude_b - latitude_a)
    longitude_delta = radians(longitude_b - longitude_a)
    value = (
        sin(latitude_delta / 2) ** 2
        + cos(radians(latitude_a))
        * cos(radians(latitude_b))
        * sin(longitude_delta / 2) ** 2
    )
    return 2 * EARTH_RADIUS_METERS * asin(sqrt(value))