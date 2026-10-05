"""Hospital suitability ranking over hospital + route pairs.

This module does not rank hospitals on their own. It pairs each
constraint-feasible hospital with the constraint-feasible Phase 5 route
candidates that actually lead to it, and scores each pair. That is the honest
version of "which hospital is best": a hospital nobody can reach quickly is not
a good destination, and a route is only meaningful relative to where it goes.

Nothing here regenerates a route. Route candidates arrive as persisted
:class:`RouteProfile` records; the only thing derived from geography is
straight-line distance, used to pair a candidate's stored destination with a
hospital and to annotate the explanation.

Determinism is structural rather than incidental: every collection is sorted
by a total key built from stored values, no set is iterated, and no clock is
read. Two runs over the same inputs produce byte-identical output.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from typing import Any
from uuid import UUID

from app.config import Settings
from app.services.optimization.constraints import (
    CapabilityMatch,
    HospitalProfile,
    Rejection,
    RouteProfile,
    Verdict,
    evaluate_hospital,
    evaluate_route,
    haversine_meters,
)
from app.services.optimization.plan_scoring import (
    PlanFactors,
    PlanOption,
    PlanScoreWeights,
    PlanScoringEngine,
)

#: Rendered in a rationale wherever a factor could not be computed. SENTINEL
#: reports "unavailable" rather than substituting a placeholder number.
UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class HospitalSuitability:
    """One hospital's standing, its viable routes, and why it stands there."""

    hospital: HospitalProfile
    capability: CapabilityMatch
    accepted: bool
    rejections: tuple[str, ...]
    options: tuple[PlanOption, ...] = ()
    rank: int | None = None
    straight_line_meters: float | None = None
    best_eta_seconds: int | None = None
    best_route_risk: float | None = None
    truncated: bool = False

    @property
    def hospital_id(self) -> UUID:
        return self.hospital.hospital_id

    def explanation(self) -> dict[str, Any]:
        """The 'why this hospital' block persisted in the plan rationale."""
        best = self.options[0] if self.options else None
        return {
            "hospital_id": str(self.hospital.hospital_id),
            "name": self.hospital.name,
            "accepted": self.accepted,
            "rejections": list(self.rejections),
            "capability_match": bool(self.capability.matched)
            and not self.capability.missing,
            "capability_matched": list(self.capability.matched),
            "capability_missing": list(self.capability.missing),
            "capability_declared_by_hospital": self.capability.subject_declares,
            "required_capabilities_declared": self.capability.requirements_declared,
            "capacity_available": self.hospital.capacity_available,
            "capacity_total": self.hospital.capacity_total,
            "capacity_available_to_mission": self.hospital.capacity_available > 0,
            "operational_status": self.hospital.operational_status,
            "emergency_capable": (
                self.hospital.emergency_capable
                if self.hospital.emergency_capable is not None
                else UNAVAILABLE
            ),
            "reliability": (
                round(self.hospital.reliability, 4)
                if self.hospital.reliability is not None
                else UNAVAILABLE
            ),
            "straight_line_meters_from_origin": (
                round(self.straight_line_meters, 1)
                if self.straight_line_meters is not None
                else UNAVAILABLE
            ),
            "viable_route_count": len(self.options),
            "routes_dropped_by_search_bound": self.truncated,
            "best_eta_seconds": (
                self.best_eta_seconds if self.best_eta_seconds is not None else UNAVAILABLE
            ),
            "best_route_risk": (
                round(self.best_route_risk, 4)
                if self.best_route_risk is not None
                else UNAVAILABLE
            ),
            "best_plan_cost": (
                round(best.score.total, 6)
                if best is not None and best.score.total is not None
                else UNAVAILABLE
            ),
            "rank": self.rank if self.rank is not None else UNAVAILABLE,
        }


@dataclass(frozen=True)
class RouteRejection:
    """A route candidate the constraints refused, and why."""

    candidate_id: UUID
    route_id: UUID | None
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class HospitalRanking:
    """The complete ranked picture: options, accepted hospitals, rejections."""

    options: tuple[PlanOption, ...] = ()
    accepted: tuple[HospitalSuitability, ...] = ()
    rejected: tuple[HospitalSuitability, ...] = ()
    dropped_by_bound: tuple[HospitalSuitability, ...] = ()
    route_rejections: tuple[RouteRejection, ...] = ()
    coverage: float = 0.0

    def rejected_explanations(self) -> list[dict[str, Any]]:
        return [
            {**entry.explanation(), "rejected_by": "search_bound" if entry.truncated else "hard_constraint"}
            for entry in self.rejected + self.dropped_by_bound
        ]


@dataclass
class _Pair:
    """An accepted hospital together with the routes that reach it."""

    hospital: HospitalProfile
    capability: CapabilityMatch
    routes: list[RouteProfile]
    straight_line_meters: float | None
    truncated: bool


class HospitalSuitabilityRanker:
    """Builds and scores the joint hospital + route option set."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.engine = PlanScoringEngine(PlanScoreWeights.from_settings(settings))

    def rank(
        self,
        hospitals: Sequence[HospitalProfile],
        routes: Sequence[RouteProfile],
        *,
        required_capabilities: Sequence[str] = (),
        origin_position: tuple[float, float] | None = None,
        resource_response_meters: float | None = None,
    ) -> HospitalRanking:
        """Filter, pair, bound, score, and order -- retaining every rejection.

        ``resource_response_meters`` is the allocator's response distance. It is
        identical for every option because resource allocation is mission-scoped
        rather than hospital-scoped, so it does not discriminate between options;
        it is carried through anyway so the objective stays complete and the
        factor is reported rather than silently dropped.
        """
        route_rejections: list[RouteRejection] = []
        viable_routes: list[RouteProfile] = []
        for route in routes:
            verdict = evaluate_route(route)
            if verdict.accepted:
                viable_routes.append(route)
            else:
                route_rejections.append(
                    RouteRejection(
                        candidate_id=route.candidate_id,
                        route_id=route.route_id,
                        reasons=verdict.reasons,
                    )
                )
        viable_routes.sort(
            key=lambda route: (route.role_order, route.route_rank, str(route.candidate_id))
        )

        pairs: list[_Pair] = []
        rejected: list[HospitalSuitability] = []
        for hospital in sorted(
            hospitals, key=lambda item: (item.name, str(item.hospital_id))
        ):
            straight_line = (
                haversine_meters(origin_position, hospital.position)
                if origin_position is not None
                else None
            )
            verdict, capability = evaluate_hospital(hospital, required_capabilities)
            if not verdict.accepted:
                rejected.append(
                    HospitalSuitability(
                        hospital=hospital,
                        capability=capability,
                        accepted=False,
                        rejections=verdict.reasons,
                        straight_line_meters=straight_line,
                    )
                )
                continue
            reaching = self._routes_to(hospital, viable_routes)
            if not reaching:
                # A hospital nothing can currently be routed to is not a usable
                # destination for this mission, whatever its clinical standing.
                rejected.append(
                    HospitalSuitability(
                        hospital=hospital,
                        capability=capability,
                        accepted=False,
                        rejections=(Rejection.HOSPITAL_NO_VIABLE_ROUTE.value,),
                        straight_line_meters=straight_line,
                    )
                )
                continue
            truncated = len(reaching) > self.settings.optimization_max_route_candidates
            pairs.append(
                _Pair(
                    hospital=hospital,
                    capability=capability,
                    routes=reaching[: self.settings.optimization_max_route_candidates],
                    straight_line_meters=straight_line,
                    truncated=truncated,
                )
            )

        pairs.sort(key=self._bound_key)
        retained = pairs[: self.settings.optimization_max_hospitals]
        dropped = pairs[self.settings.optimization_max_hospitals :]

        options = self._build_options(retained, resource_response_meters)

        best_by_hospital: dict[UUID, PlanOption] = {}
        for option in options:
            current = best_by_hospital.get(option.hospital_id)
            if current is None or option.ordering_key() < current.ordering_key():
                best_by_hospital[option.hospital_id] = option

        def order_key(pair: _Pair) -> tuple[Any, ...]:
            best = best_by_hospital.get(pair.hospital.hospital_id)
            return best.ordering_key() if best is not None else (float("inf"), 9, 9, "", "")

        ranked = [
            HospitalSuitability(
                hospital=pair.hospital,
                capability=pair.capability,
                accepted=True,
                rejections=(),
                options=tuple(
                    option
                    for option in options
                    if option.hospital_id == pair.hospital.hospital_id
                ),
                straight_line_meters=pair.straight_line_meters,
                best_eta_seconds=best_by_hospital[pair.hospital.hospital_id].factors.eta_seconds
                if pair.hospital.hospital_id in best_by_hospital
                else None,
                best_route_risk=best_by_hospital[pair.hospital.hospital_id].factors.route_risk
                if pair.hospital.hospital_id in best_by_hospital
                else None,
                truncated=pair.truncated,
            )
            for pair in sorted(retained, key=order_key)
        ]
        ranked = [replace(entry, rank=index) for index, entry in enumerate(ranked, start=1)]

        return HospitalRanking(
            options=options,
            accepted=tuple(ranked),
            rejected=tuple(rejected),
            dropped_by_bound=tuple(
                HospitalSuitability(
                    hospital=pair.hospital,
                    capability=pair.capability,
                    accepted=True,
                    rejections=(),
                    straight_line_meters=pair.straight_line_meters,
                    truncated=pair.truncated,
                )
                for pair in dropped
            ),
            route_rejections=tuple(
                sorted(route_rejections, key=lambda item: str(item.candidate_id))
            ),
            coverage=max((option.score.coverage for option in options), default=0.0),
        )

    # -- construction --------------------------------------------------

    def _build_options(
        self, pairs: Sequence[_Pair], resource_response_meters: float | None
    ) -> tuple[PlanOption, ...]:
        """One scored option per surviving hospital + route pair."""
        raw: list[tuple[HospitalProfile, CapabilityMatch, RouteProfile, PlanFactors]] = []
        for pair in pairs:
            for route in pair.routes:
                raw.append(
                    (
                        pair.hospital,
                        pair.capability,
                        route,
                        PlanFactors(
                            eta_seconds=route.estimated_duration_seconds,
                            capability_matched=len(pair.capability.matched),
                            capability_required=len(pair.capability.required),
                            capacity_available=pair.hospital.capacity_available,
                            capacity_total=pair.hospital.capacity_total,
                            route_risk=_route_risk(route),
                            reliability=pair.hospital.reliability,
                            resource_response_meters=resource_response_meters,
                        ),
                    )
                )
        costs = self.engine.normalize([item[3] for item in raw])
        options = [
            PlanOption(
                hospital=hospital,
                route=route,
                capability=capability,
                factors=factors,
                score=self.engine.score(cost),
            )
            for (hospital, capability, route, factors), cost in zip(raw, costs)
        ]
        options.sort(key=lambda option: option.ordering_key())
        return tuple(options)

    # -- helpers -------------------------------------------------------

    def _bound_key(self, pair: _Pair) -> tuple[Any, ...]:
        """Order used when the hospital search bound must discard candidates.

        Applied on hard-constraint standing only -- unmet capability count,
        then free capacity, then a stable name -- because travel time is not
        known until the options are built. A bound applied this way drops the
        least capable hospitals first, which is a stated limitation rather than
        an arbitrary cut.
        """
        return (
            len(pair.capability.missing),
            -pair.hospital.capacity_available,
            pair.hospital.name,
            str(pair.hospital.hospital_id),
        )

    def _routes_to(
        self, hospital: HospitalProfile, routes: Sequence[RouteProfile]
    ) -> list[RouteProfile]:
        """Route candidates whose stored destination is this hospital."""
        matched: list[RouteProfile] = []
        for route in routes:
            if route.destination_latitude is None or route.destination_longitude is None:
                continue
            distance = haversine_meters(
                (route.destination_longitude, route.destination_latitude),
                hospital.position,
            )
            if distance <= self.settings.optimization_hospital_match_meters:
                matched.append(route)
        return matched


def _route_risk(route: RouteProfile) -> float | None:
    """Prefer a predicted failure probability, fall back to route risk, else None.

    Same preference order Phase 5 used when persisting candidates, so the
    optimizer and the routing layer never disagree about which number is the
    better one. No value is invented when neither signal exists.
    """
    if route.predicted_failure_probability is not None:
        return route.predicted_failure_probability
    return route.risk_score
