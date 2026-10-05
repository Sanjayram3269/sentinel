"""Deterministic resource allocation.

Resources in SENTINEL are :class:`~app.models.vehicle.Vehicle` records; there is
no separate resource entity and none is introduced here.

The allocator answers one question -- "which vehicles satisfy this mission's
stated requirements?" -- and it does so with a fixed rule rather than a search:
for each requirement slot, take the nearest acceptable resource, breaking ties
by call sign and then id. That ordering is total and value-based, so the result
cannot depend on database row order, set iteration, or a clock.

Two behaviours are deliberate and worth stating plainly:

* **No requirements means no resources.** SENTINEL's mission model carries no
  clinical requirement field, so when a caller states none, the allocator
  allocates none and says the requirement was absent. It does not help itself to
  a "reasonable default" vehicle, because a default assignment is a fabricated
  dispatch decision wearing a log message.
* **An unmet requirement is a failure, not a downgrade.** When a required
  vehicle type has no acceptable resource, the allocation is infeasible and the
  mission cannot be planned, rather than proceeding with fewer resources than
  the mission asked for.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from uuid import UUID

from app.services.optimization.constraints import (
    CapabilityMatch,
    Rejection,
    ResourceProfile,
    Verdict,
    evaluate_resource,
    haversine_meters,
)


@dataclass(frozen=True)
class ResourceSelection:
    """A resource assigned to one requirement slot."""

    requirement: str
    resource: ResourceProfile
    response_meters: float | None
    reasons: tuple[str, ...]

    @property
    def vehicle_id(self) -> UUID:
        return self.resource.vehicle_id


@dataclass(frozen=True)
class ResourceRejection:
    """A resource that was considered and not selected, with the reason."""

    resource: ResourceProfile
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class UnmetRequirement:
    """A requirement slot that no resource could fill."""

    requirement: str
    considered: tuple[str, ...]

    @property
    def reason(self) -> str:
        return Rejection.REQUIREMENT_UNSATISFIED.value


@dataclass(frozen=True)
class ResourceAllocation:
    """The allocator's full, explainable result."""

    feasible: bool
    selections: tuple[ResourceSelection, ...] = ()
    rejections: tuple[ResourceRejection, ...] = ()
    unmet: tuple[UnmetRequirement, ...] = ()
    requirements_declared: bool = False
    missing: tuple[str, ...] = ()

    @property
    def selected_ids(self) -> tuple[UUID, ...]:
        return tuple(selection.vehicle_id for selection in self.selections)

    @property
    def response_meters(self) -> float | None:
        """Worst straight-line response distance among selected resources.

        Straight-line, not travel time: allocating a resource does not generate
        a route, and inventing one here would duplicate Phase 5 routing inside
        the decision layer.
        """
        known = [
            selection.response_meters
            for selection in self.selections
            if selection.response_meters is not None
        ]
        if len(known) != len(self.selections) or not known:
            return None
        return max(known)


@dataclass
class _Slot:
    """One requirement to be satisfied by exactly one resource."""

    key: str
    vehicle_type: str | None
    capabilities: tuple[str, ...]
    selected: ResourceSelection | None = None
    considered: list[str] = field(default_factory=list)


def _requirement_slots(
    required_vehicle_types: Iterable[str],
    required_capabilities: Iterable[str],
) -> list[_Slot]:
    """Build the ordered requirement slots for a mission.

    One slot per distinct required vehicle type. When capabilities are required
    but no vehicle type is, a single slot asks for any resource declaring them,
    which is the only reading the stated data supports.
    """
    types = sorted({item.strip().upper() for item in required_vehicle_types if str(item).strip()})
    capabilities = tuple(
        sorted({item.strip().lower() for item in required_capabilities if str(item).strip()})
    )
    if types:
        return [
            _Slot(key=f"vehicle_type:{item}", vehicle_type=item, capabilities=capabilities)
            for item in types
        ]
    if capabilities:
        return [
            _Slot(
                key="capability:" + "+".join(capabilities),
                vehicle_type=None,
                capabilities=capabilities,
            )
        ]
    return []


class ResourceAllocator:
    """Assigns vehicles to requirement slots under the hard constraints."""

    def __init__(self, mission_id: UUID) -> None:
        self.mission_id = mission_id

    def allocate(
        self,
        resources: Sequence[ResourceProfile],
        *,
        required_vehicle_types: Iterable[str] = (),
        required_capabilities: Iterable[str] = (),
        incident_position: tuple[float, float] | None = None,
    ) -> ResourceAllocation:
        """Assign resources, or explain precisely why no assignment exists."""
        slots = _requirement_slots(required_vehicle_types, required_capabilities)
        declared = bool(slots)
        if not declared:
            # No requirement was stated. Report every resource's standing so the
            # plan can still say why nothing was dispatched.
            return ResourceAllocation(
                feasible=True,
                selections=(),
                rejections=self._rejections_only(resources, ()),
                requirements_declared=False,
                missing=("required_vehicle_types", "required_capabilities"),
            )

        accepted: list[tuple[ResourceProfile, CapabilityMatch]] = []
        rejected: list[ResourceRejection] = []
        for resource in self._sorted(resources):
            verdict, match = evaluate_resource(
                resource,
                mission_id=self.mission_id,
                required_vehicle_types=(),
                required_capabilities=(),
            )
            if verdict.accepted:
                accepted.append((resource, match))
            else:
                rejected.append(ResourceRejection(resource, verdict.reasons))

        unmet: list[UnmetRequirement] = []
        selected: list[ResourceSelection] = []
        claimed: set[UUID] = set()
        for slot in slots:
            candidate = self._best_for_slot(slot, accepted, claimed, incident_position)
            if candidate is None:
                unmet.append(
                    UnmetRequirement(requirement=slot.key, considered=tuple(sorted(slot.considered)))
                )
                continue
            resource, match = candidate
            claimed.add(resource.vehicle_id)
            response = self._response_meters(resource, incident_position)
            selected.append(
                ResourceSelection(
                    requirement=slot.key,
                    resource=resource,
                    response_meters=response,
                    reasons=(
                        f"available and uncommitted at allocation time",
                        f"matches vehicle_type {slot.vehicle_type or 'any'}",
                        *(
                            [f"declares capabilities {', '.join(match.matched)}"]
                            if match.matched
                            else []
                        ),
                        *(
                            [
                                "response distance unavailable: resource or incident position unknown"
                            ]
                            if response is None
                            else [f"straight-line response {response:.0f} m"]
                        ),
                    ),
                )
            )

        feasible = not unmet
        return ResourceAllocation(
            feasible=feasible,
            selections=tuple(selected),
            rejections=tuple(rejected)
            + tuple(
                ResourceRejection(resource, (Rejection.REQUIREMENT_UNSATISFIED.value,))
                for resource, _ in accepted
                if resource.vehicle_id not in claimed
            ),
            unmet=tuple(unmet),
            requirements_declared=True,
        )

    # -- helpers -------------------------------------------------------

    @staticmethod
    def _sorted(resources: Sequence[ResourceProfile]) -> list[ResourceProfile]:
        """A total order that does not depend on the caller's row order."""
        return sorted(resources, key=lambda item: (item.call_sign, str(item.vehicle_id)))

    def _rejections_only(
        self,
        resources: Sequence[ResourceProfile],
        required_capabilities: Iterable[str],
    ) -> tuple[ResourceRejection, ...]:
        output: list[ResourceRejection] = []
        for resource in self._sorted(resources):
            verdict, _ = evaluate_resource(
                resource,
                mission_id=self.mission_id,
                required_vehicle_types=(),
                required_capabilities=required_capabilities,
            )
            if not verdict.accepted:
                output.append(ResourceRejection(resource, verdict.reasons))
        return tuple(output)

    def _best_for_slot(
        self,
        slot: _Slot,
        accepted: Sequence[tuple[ResourceProfile, CapabilityMatch]],
        claimed: set[UUID],
        incident_position: tuple[float, float] | None,
    ) -> tuple[ResourceProfile, CapabilityMatch] | None:
        """Pick one resource for a slot, or None if nothing qualifies.

        A resource already claimed by an earlier slot is skipped, which is what
        stops a single vehicle from satisfying two requirements at once. A
        candidate whose capability requirement cannot be verified is not used:
        capability is a hard constraint here exactly as it is for hospitals.
        """
        best: tuple[tuple[float, str, str], ResourceProfile, CapabilityMatch] | None = None
        for resource, _ in accepted:
            if resource.vehicle_id in claimed:
                continue
            slot.considered.append(resource.call_sign)
            if slot.vehicle_type is not None and resource.vehicle_type.upper() != slot.vehicle_type:
                continue
            if slot.capabilities:
                match = _match_only(resource, slot.capabilities)
                if match.missing:
                    continue
            else:
                match = CapabilityMatch(
                    required=(),
                    matched=(),
                    missing=(),
                    requirements_declared=False,
                    subject_declares=bool(resource.declared_capabilities),
                )
            response = self._response_meters(resource, incident_position)
            # Unknown distance sorts last but is still eligible: refusing to
            # dispatch a resource because its position was not recorded would
            # discard real capability.
            distance = response if response is not None else float("inf")
            key = (distance, resource.call_sign, str(resource.vehicle_id))
            if best is None or key < best[0]:
                best = (key, resource, match)
        if best is None:
            return None
        return best[1], best[2]

    @staticmethod
    def _response_meters(
        resource: ResourceProfile, incident_position: tuple[float, float] | None
    ) -> float | None:
        position = resource.position
        if position is None or incident_position is None:
            return None
        return haversine_meters(position, incident_position)


def _match_only(resource: ResourceProfile, capabilities: Iterable[str]) -> CapabilityMatch:
    from app.services.optimization.constraints import match_capabilities

    return match_capabilities(
        capabilities, resource.declared_capabilities, requirements_declared=True
    )


__all__ = [
    "ResourceAllocation",
    "ResourceAllocator",
    "ResourceRejection",
    "ResourceSelection",
    "UnmetRequirement",
    "Verdict",
]
