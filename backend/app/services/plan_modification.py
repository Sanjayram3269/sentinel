"""Reviewer-driven plan modification: supersede, then re-offer for review.

A modification never edits a plan in place. It writes a **new version** and
marks the old one ``SUPERSEDED``, which is what makes the approval rule hold:
the approval bound to version 4 cannot travel to version 5, because version 4
is a frozen historical record and version 5 has no approval at all.

Only the three selections a human is actually entitled to change are copied
across (route, hospital, resources). Everything the optimizer *computed* -- the
score, feasibility, objective, rationale, and the constraint findings that
produced it -- is carried forward untouched. A reviewer who overrode the route
has invalidated the old travel-time contribution, and the new version says so
in its rationale rather than inheriting a score that no longer describes it.
"""

from __future__ import annotations

import copy
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import MissionPlan, Route
from app.models.enums import ApprovalStatus, PlanStatus
from app.schemas.approval import PlanModification
from app.schemas.events import EventCreate, EventType
from app.services.approval import (
    APPROVAL_SOURCE,
    REVIEWER_IDENTITY_KIND,
    ApprovalCode,
    ApprovalError,
)
from app.services.event_service import EventService

logger = logging.getLogger(__name__)

MODIFICATION_SOURCE = "sentinel_plan_modification"


@dataclass(frozen=True)
class ModificationResult:
    previous_plan_id: UUID
    previous_plan_version: int
    previous_plan_status: str
    new_plan_id: UUID
    new_plan_version: int
    new_plan_status: str
    applied: list[dict[str, Any]]
    requires_human_approval: bool = True
    previous_approval_invalidated: bool = True


def _apply_to_payload(
    payload: dict[str, Any], modifications: list[PlanModification]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Return a new payload with only the permitted selections replaced."""
    updated = copy.deepcopy(payload)
    selected = updated.setdefault("selected", {})
    applied: list[dict[str, Any]] = []

    for item in modifications:
        if item.field.value == "ROUTE":
            applied.append(
                {"field": "ROUTE", "from": selected.get("route_id"), "to": str(item.value)}
            )
            selected["route_id"] = str(item.value) if item.value else None
            route_section = updated.setdefault("route", {})
            route_section["route_id"] = str(item.value) if item.value else None
            route_section["reviewer_overridden"] = True
        elif item.field.value == "HOSPITAL":
            applied.append(
                {
                    "field": "HOSPITAL",
                    "from": selected.get("hospital_id"),
                    "to": str(item.value),
                }
            )
            selected["hospital_id"] = str(item.value) if item.value else None
        elif item.field.value == "RESOURCE":
            values = [str(value) for value in (item.value_list or [])]
            applied.append(
                {"field": "RESOURCE", "from": selected.get("resource_ids"), "to": values}
            )
            selected["resource_ids"] = values

    # The optimizer's own conclusions described the old selection. Say so
    # instead of letting a stale score travel with a new route.
    updated["reviewer_modification"] = {
        "applied_at": datetime.now(timezone.utc).isoformat(),
        "modifications": applied,
        "score_note": (
            "Carried forward from the superseded version and no longer describes "
            "this selection. Recompute before relying on it."
        ),
    }
    return updated, applied


class PlanModificationService:
    """Create a new plan version from a reviewer's overrides."""

    def __init__(self, db: AsyncSession, event_service: EventService) -> None:
        self.db = db
        self.event_service = event_service

    async def modify(
        self,
        db: AsyncSession,
        mission_id: UUID,
        plan_id: UUID,
        *,
        plan_version: int,
        reviewer_id: str,
        modifications: list[PlanModification],
        comment: str | None = None,
    ) -> ModificationResult:
        from app.models.plan import PlanApproval

        plan = await db.get(MissionPlan, plan_id)
        if plan is None or plan.mission_id != mission_id:
            raise ApprovalError(ApprovalCode.PLAN_NOT_FOUND, "Mission plan not found")
        if plan.version != plan_version:
            raise ApprovalError(
                ApprovalCode.PLAN_VERSION_MISMATCH,
                f"Modification targets version {plan_version} but the current plan is version {plan.version}",
            )
        if plan.status is PlanStatus.SUPERSEDED:
            raise ApprovalError(
                ApprovalCode.PLAN_SUPERSEDED, "Plan version was already superseded"
            )

        for item in modifications:
            if item.field.value == "ROUTE":
                await self._require_route(db, item.value)

        next_version = int(
            await db.scalar(
                select(func.max(MissionPlan.version)).where(
                    MissionPlan.mission_id == mission_id
                )
            )
            or plan.version
        ) + 1

        previous_status = plan.status
        payload, applied = _apply_to_payload(plan.plan_payload or {}, modifications)

        reasons = [
            f"{item.field.value} overridden by reviewer"
            for item in modifications
        ]
        if comment:
            reasons.append(comment)

        new_plan = MissionPlan(
            mission_id=plan.mission_id,
            version=next_version,
            status=PlanStatus.READY_FOR_REVIEW,
            objective=plan.objective,
            plan_payload=payload,
            # Carried forward, explicitly flagged in the payload as describing
            # the superseded selection rather than this one.
            score=plan.score,
            confidence=plan.confidence,
            feasible=plan.feasible,
            rationale=(
                f"Supersedes version {plan.version}. Reviewer modification: "
                + "; ".join(reasons)
            ),
            network_key=plan.network_key,
            network_checksum=plan.network_checksum,
        )
        db.add(new_plan)
        plan.status = PlanStatus.SUPERSEDED
        await db.flush()
        await db.refresh(new_plan)

        db.add(
            PlanApproval(
                plan_id=new_plan.id,
                plan_version=next_version,
                # MODIFIED records that a human changed this version. It is not
                # an approval and never authorizes execution.
                status=ApprovalStatus.MODIFIED,
                decision=ApprovalStatus.MODIFIED.value,
                reviewer_id=reviewer_id,
                previous_plan_status=previous_status.value,
                new_plan_status=PlanStatus.READY_FOR_REVIEW.value,
                comment=comment,
                evidence={"applied_modifications": applied},
                context={
                    "reviewer_identity_kind": REVIEWER_IDENTITY_KIND,
                    "superseded_plan_id": str(plan.id),
                    "superseded_version": plan.version,
                },
                decided_at=datetime.now(timezone.utc),
            )
        )

        event = await self.event_service.persist(
            db,
            mission_id,
            EventCreate(
                event_type=EventType.PLAN_CREATED,
                timestamp=datetime.now(timezone.utc),
                source=MODIFICATION_SOURCE,
                correlation_id=new_plan.id,
                payload={
                    "action": "PLAN_MODIFIED",
                    "previous_plan_id": str(plan.id),
                    "previous_plan_version": plan.version,
                    "previous_plan_status": previous_status.value,
                    "new_plan_id": str(new_plan.id),
                    "new_plan_version": next_version,
                    "new_plan_status": new_plan.status.value,
                    "applied_modifications": applied,
                    "reviewer_id": reviewer_id,
                    "requires_human_approval": True,
                },
            ),
        )
        try:
            await self.event_service.publish_persisted(event)
        except Exception:  # pragma: no cover - notification is best effort
            logger.warning("Modification event %s persisted but not published", event.id)

        return ModificationResult(
            previous_plan_id=plan.id,
            previous_plan_version=plan.version,
            previous_plan_status=previous_status.value,
            new_plan_id=new_plan.id,
            new_plan_version=next_version,
            new_plan_status=new_plan.status.value,
            applied=applied,
        )

    async def _require_route(self, db: AsyncSession, route_id: UUID | None) -> None:
        if route_id is None:
            return
        route = await db.get(Route, route_id)
        if route is None:
            raise ApprovalError(
                ApprovalCode.MODIFICATION_NOT_ALLOWED,
                f"Route {route_id} does not exist and cannot be selected",
            )


__all__ = ["MODIFICATION_SOURCE", "ModificationResult", "PlanModificationService"]
