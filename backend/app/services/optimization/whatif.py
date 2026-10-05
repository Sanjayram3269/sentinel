"""Deterministic what-if analysis for an existing mission plan.

Answers one question: *if this selected component became unavailable, what
would the plan be instead?* The mechanism is deliberately the simplest one that
is correct -- run the same optimizer again with that one component excluded,
then diff the two outcomes. Because the optimizer is deterministic, any
difference in the result is attributable to the excluded component and nothing
else.

Scope, and why it stops here
---------------------------
The deferred ``abhy_ai`` counterfactual module could also close a road edge, add
a hazard, or scale demand. Those are **not** implemented in Phase 6, and the
reason is structural rather than incidental: each one mutates the world the
router reads, so answering it correctly would require re-running Phase 5 route
generation over a modified graph. That is a second, expensive search whose
result would depend on when it ran, and folding it into the decision layer
would break the guarantee this phase is built on -- that the optimizer never
regenerates a route. They are deferred, not overlooked.
"""

from dataclasses import dataclass, replace
from enum import Enum
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import MissionPlan
from app.services.optimization.mission_optimizer import (
    MissionOptimizer,
    MissionPlanOutcome,
    OptimizationRequest,
)


class WhatIfComponent(str, Enum):
    """The three things a plan selects, and so the three things that can fail."""

    HOSPITAL = "HOSPITAL"
    ROUTE = "ROUTE"
    RESOURCE = "RESOURCE"


@dataclass(frozen=True)
class WhatIfChange:
    """One component removed from the candidate space."""

    component: WhatIfComponent
    component_id: UUID

    def apply(self, request: OptimizationRequest) -> OptimizationRequest:
        if self.component is WhatIfComponent.HOSPITAL:
            return replace(
                request, unavailable_hospital_ids=frozenset({self.component_id})
            )
        if self.component is WhatIfComponent.ROUTE:
            return replace(request, unavailable_route_ids=frozenset({self.component_id}))
        return replace(request, unavailable_vehicle_ids=frozenset({self.component_id}))


@dataclass(frozen=True)
class WhatIfResult:
    """Baseline plan, recomputed plan, and an explicit statement of the delta."""

    component: WhatIfComponent
    component_id: UUID
    baseline_feasible: bool
    recomputed_feasible: bool
    hospital_changed: bool
    route_changed: bool
    resources_changed: bool
    plan_changed: bool
    baseline_hospital_id: str | None
    recomputed_hospital_id: str | None
    baseline_route_id: str | None
    recomputed_route_id: str | None
    baseline_resource_ids: tuple[str, ...]
    recomputed_resource_ids: tuple[str, ...]
    baseline_eta_seconds: int | None
    recomputed_eta_seconds: int | None
    delta_eta_seconds: int | None
    baseline_cost: float | None
    recomputed_cost: float | None
    explanation: str
    recomputed: MissionPlanOutcome

    def to_payload(self) -> dict[str, Any]:
        return {
            "component": self.component.value,
            "component_id": str(self.component_id),
            "baseline_feasible": self.baseline_feasible,
            "recomputed_feasible": self.recomputed_feasible,
            "plan_changed": self.plan_changed,
            "hospital_changed": self.hospital_changed,
            "route_changed": self.route_changed,
            "resources_changed": self.resources_changed,
            "baseline": {
                "hospital_id": self.baseline_hospital_id,
                "route_id": self.baseline_route_id,
                "resource_ids": list(self.baseline_resource_ids),
                "eta_seconds": self.baseline_eta_seconds,
                "plan_cost": self.baseline_cost,
            },
            "recomputed": {
                "hospital_id": self.recomputed_hospital_id,
                "route_id": self.recomputed_route_id,
                "resource_ids": list(self.recomputed_resource_ids),
                "eta_seconds": self.recomputed_eta_seconds,
                "plan_cost": self.recomputed_cost,
            },
            "delta_eta_seconds": self.delta_eta_seconds,
            "explanation": self.explanation,
            "recomputed_infeasible_reasons": self.recomputed.payload["infeasible_reasons"],
            "recomputed_rationale": self.recomputed.rationale,
        }


class WhatIfService:
    """Recomputes a plan with one selected component removed."""

    def __init__(self, optimizer: MissionOptimizer) -> None:
        self.optimizer = optimizer

    async def replan_without(
        self,
        db: AsyncSession,
        mission_id: UUID,
        change: WhatIfChange,
        *,
        baseline_plan_id: UUID | None = None,
        request: OptimizationRequest | None = None,
    ) -> WhatIfResult:
        """Return what the plan would be if ``change`` were unavailable.

        The baseline is read from ``baseline_plan_id`` when given, otherwise it
        is recomputed under the same requirements so the comparison is always
        like-for-like. The recomputed plan is returned but **not** persisted:
        a hypothetical is not a plan version, and writing it would pollute the
        mission's plan history with a decision nobody took.
        """
        base_request = request or OptimizationRequest.build()
        if baseline_plan_id is not None:
            baseline = await self._load_baseline(db, baseline_plan_id)
        else:
            baseline = await self.optimizer.optimize(db, mission_id, base_request, persist=False)

        recomputed = await self.optimizer.optimize(
            db, mission_id, change.apply(base_request), persist=False
        )
        return self._compare(baseline, recomputed, change)

    async def _load_baseline(
        self, db: AsyncSession, plan_id: UUID
    ) -> MissionPlanOutcome:
        plan = await db.get(MissionPlan, plan_id)
        if plan is None:
            raise LookupError(f"Mission plan {plan_id} not found")
        payload = plan.plan_payload or {}
        return MissionPlanOutcome(
            mission_id=plan.mission_id,
            feasible=bool(plan.feasible),
            objective=plan.objective,
            rationale=plan.rationale or "",
            payload=payload,
            score=plan.score,
            plan_id=plan.id,
            version=plan.version,
            status=plan.status,
            selected_option=None,
            correlation_id=None,
        )

    def _compare(
        self,
        baseline: MissionPlanOutcome,
        recomputed: MissionPlanOutcome,
        change: WhatIfChange,
    ) -> WhatIfResult:
        baseline_selected = baseline.payload.get("selected", {})
        recomputed_selected = recomputed.payload.get("selected", {})
        baseline_resources = tuple(baseline_selected.get("resource_ids") or ())
        recomputed_resources = tuple(recomputed_selected.get("resource_ids") or ())

        hospital_changed = baseline_selected.get("hospital_id") != recomputed_selected.get(
            "hospital_id"
        )
        route_changed = baseline_selected.get("route_id") != recomputed_selected.get("route_id")
        resources_changed = baseline_resources != recomputed_resources

        baseline_eta = _eta(baseline)
        recomputed_eta = _eta(recomputed)
        delta = (
            None
            if baseline_eta is None or recomputed_eta is None
            else recomputed_eta - baseline_eta
        )

        if not recomputed.feasible:
            explanation = (
                f"Removing {change.component.value.lower()} {change.component_id} leaves the "
                "mission with no feasible plan: "
                + "; ".join(recomputed.payload.get("infeasible_reasons") or ["unknown"])
                + "."
            )
        elif not (hospital_changed or route_changed or resources_changed):
            explanation = (
                f"Removing {change.component.value.lower()} {change.component_id} does not "
                "change the selected plan; the remaining options are no better."
            )
        else:
            moves: list[str] = []
            if hospital_changed:
                moves.append(
                    f"hospital {baseline_selected.get('hospital_id')} -> "
                    f"{recomputed_selected.get('hospital_id')}"
                )
            if route_changed:
                moves.append(
                    f"route {baseline_selected.get('route_id')} -> "
                    f"{recomputed_selected.get('route_id')}"
                )
            if resources_changed:
                moves.append(f"resources {list(baseline_resources)} -> {list(recomputed_resources)}")
            explanation = (
                f"Removing {change.component.value.lower()} {change.component_id} changes the "
                "plan: " + "; ".join(moves) + "."
            )

        return WhatIfResult(
            component=change.component,
            component_id=change.component_id,
            baseline_feasible=baseline.feasible,
            recomputed_feasible=recomputed.feasible,
            hospital_changed=bool(hospital_changed),
            route_changed=bool(route_changed),
            resources_changed=bool(resources_changed),
            plan_changed=bool(
                hospital_changed or route_changed or resources_changed or not recomputed.feasible
            ),
            baseline_hospital_id=baseline_selected.get("hospital_id"),
            recomputed_hospital_id=recomputed_selected.get("hospital_id"),
            baseline_route_id=baseline_selected.get("route_id"),
            recomputed_route_id=recomputed_selected.get("route_id"),
            baseline_resource_ids=baseline_resources,
            recomputed_resource_ids=recomputed_resources,
            baseline_eta_seconds=baseline_eta,
            recomputed_eta_seconds=recomputed_eta,
            delta_eta_seconds=delta,
            baseline_cost=baseline.score,
            recomputed_cost=recomputed.score,
            explanation=explanation,
            recomputed=recomputed,
        )


def _eta(outcome: MissionPlanOutcome) -> int | None:
    route = outcome.payload.get("route")
    if not isinstance(route, dict):
        return None
    value = route.get("eta_seconds")
    return int(value) if isinstance(value, (int, float)) else None
