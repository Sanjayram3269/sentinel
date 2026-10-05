"""Hard constraints for mission optimization.

Every function in this module is a *filter*. It answers "can this option be
used at all?" and nothing else: no weight, no normalisation, no ordering, no
tie-breaking. That separation is the point of the module -- it is what makes
"this hospital is impossible" and "this hospital is merely worse" two
different, independently testable things.

Every rejection carries a machine-readable code from :class:`Rejection` so the
optimizer can persist an explanation without parsing prose.

These functions are pure and operate on profile dataclasses rather than ORM
objects, so the constraint layer can be tested exhaustively without a database.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import Enum
from math import asin, cos, radians, sin, sqrt
from typing import Any
from uuid import UUID

EARTH_RADIUS_METERS = 6_371_008.8

#: SENTINEL stores capabilities as free-form JSONB and has never validated its
#: shape, so several key spellings exist in the database already. All of them
#: are read. Nothing is inferred from a key that is absent.
CAPABILITY_KEYS = ("capabilities", "specialties", "services")

#: A vehicle counts as available only in this state. ``EN_ROUTE``, ``AT_SCENE``,
#: ``TRANSPORTING`` and ``OFFLINE`` all mean it is committed elsewhere.
AVAILABLE_RESOURCE_STATUS = "AVAILABLE"


class Rejection(str, Enum):
    """Machine-readable hard-constraint rejection codes."""

    HOSPITAL_NOT_OPERATIONAL = "hospital_not_operational"
    HOSPITAL_CAPACITY_UNAVAILABLE = "hospital_capacity_unavailable"
    HOSPITAL_CAPABILITY_MISMATCH = "hospital_capability_mismatch"
    HOSPITAL_CAPABILITY_UNKNOWN = "hospital_capability_unknown"
    HOSPITAL_NO_VIABLE_ROUTE = "hospital_no_viable_route"

    ROUTE_STATUS_NON_VIABLE = "route_status_non_viable"
    ROUTE_REJECTED_BY_ROUTE_SCORING = "route_rejected_by_route_scoring"
    ROUTE_GEOMETRY_MISSING = "route_geometry_missing"

    RESOURCE_UNAVAILABLE = "resource_unavailable"
    RESOURCE_COMMITTED_ELSEWHERE = "resource_committed_to_another_mission"
    RESOURCE_TYPE_MISMATCH = "resource_type_mismatch"
    RESOURCE_CAPABILITY_MISMATCH = "resource_capability_mismatch"
    RESOURCE_CAPABILITY_UNKNOWN = "resource_capability_unknown"

    REQUIREMENT_UNSATISFIED = "requirement_unsatisfied"


def declared_capabilities(payload: Mapping[str, Any] | None) -> tuple[str, ...]:
    """Return the sorted, lowercased capability labels a record declares.

    An empty tuple means "declares nothing", which is *not* the same as
    "supports nothing": callers must treat it as unknown and say so.
    """
    if not isinstance(payload, Mapping):
        return ()
    labels: set[str] = set()
    for key in CAPABILITY_KEYS:
        value = payload.get(key)
        if isinstance(value, (list, tuple, set)):
            labels.update(
                str(item).strip().lower() for item in value if str(item).strip()
            )
        elif isinstance(value, str) and value.strip():
            labels.add(value.strip().lower())

    # Also support the existing JSONB convention where capabilities are
    # declared directly as boolean keys, e.g. {"trauma": true}.
    for key, value in payload.items():
        if key.lower() not in CAPABILITY_KEYS and value is True:
            labels.add(str(key).strip().lower())

    return tuple(sorted(labels))


def declares_emergency(payload: Mapping[str, Any] | None) -> bool | None:
    """Return an explicitly declared emergency flag, or None if undeclared."""
    if not isinstance(payload, Mapping):
        return None
    for key in ("emergency", "emergency_capable", "is_emergency"):
        if key in payload:
            return bool(payload[key])
    return None


def haversine_meters(
    first: tuple[float, float], second: tuple[float, float]
) -> float:
    """Great-circle distance in metres between two (lon, lat) pairs."""
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


@dataclass(frozen=True)
class HospitalProfile:
    """Everything the constraint layer is allowed to know about a hospital."""

    hospital_id: UUID
    name: str
    latitude: float
    longitude: float
    capacity_total: int
    capacity_available: int
    operational_status: str
    declared_capabilities: tuple[str, ...] = ()
    emergency_capable: bool | None = None
    reliability: float | None = None

    @property
    def capabilities_declared(self) -> bool:
        return bool(self.declared_capabilities)

    @property
    def position(self) -> tuple[float, float]:
        return (self.longitude, self.latitude)


@dataclass(frozen=True)
class RouteProfile:
    """A persisted Phase 5 route candidate, reduced to constraint inputs."""

    candidate_id: UUID
    route_id: UUID | None
    vehicle_id: UUID
    planning_cycle_id: UUID
    status: str
    route_rank: int
    estimated_duration_seconds: int
    distance_meters: float
    route_score_viable: bool
    resilience_role: str | None = None
    destination_latitude: float | None = None
    destination_longitude: float | None = None
    origin_latitude: float | None = None
    origin_longitude: float | None = None
    geometry_points: int = 0
    # Phase 5 signals, carried through unchanged. The optimizer never
    # recomputes them; it only chooses which stored value to prefer.
    risk_score: float | None = None
    predicted_failure_probability: float | None = None
    #: Canonical ``RoadEdge.id`` values Phase 5 recorded for this candidate,
    #: carried through untouched so a plan can be traced back to real edges.
    road_segment_ids: tuple[str, ...] = ()

    @property
    def role_order(self) -> int:
        """Sort key preferring PRIMARY, then BACKUP, then CONTINGENCY."""
        return {"PRIMARY": 0, "BACKUP": 1, "CONTINGENCY": 2}.get(
            self.resilience_role or "", 3
        )


@dataclass(frozen=True)
class ResourceProfile:
    """A vehicle considered as a mission resource."""

    vehicle_id: UUID
    call_sign: str
    vehicle_type: str
    status: str
    mission_id: UUID | None = None
    declared_capabilities: tuple[str, ...] = ()
    latitude: float | None = None
    longitude: float | None = None

    @property
    def has_location(self) -> bool:
        return self.latitude is not None and self.longitude is not None

    @property
    def position(self) -> tuple[float, float] | None:
        if self.latitude is None or self.longitude is None:
            return None
        return (self.longitude, self.latitude)


@dataclass(frozen=True)
class Verdict:
    """Outcome of a constraint check: accepted, or a tuple of rejection codes."""

    accepted: bool
    rejections: tuple[Rejection, ...] = ()

    @property
    def reasons(self) -> tuple[str, ...]:
        return tuple(rejection.value for rejection in self.rejections)


ACCEPTED = Verdict(accepted=True)


@dataclass(frozen=True)
class CapabilityMatch:
    """How a record's declared capabilities relate to what was asked for."""

    required: tuple[str, ...]
    matched: tuple[str, ...]
    missing: tuple[str, ...]
    requirements_declared: bool
    subject_declares: bool

    @property
    def satisfied(self) -> bool:
        """True when nothing is required, or every requirement is matched."""
        return not self.missing and (self.requirements_declared or self.subject_declares)


def match_capabilities(
    required: Iterable[str],
    subject_declared: tuple[str, ...],
    *,
    requirements_declared: bool,
) -> CapabilityMatch:
    """Compare requested capabilities against declared ones.

    ``requirements_declared`` and ``subject_declares`` are tracked separately
    because they fail differently and must be explained differently: a missing
    requirement is "not asked for", while a subject that declares nothing is
    "cannot be verified". Neither is silently upgraded into a match.
    """
    wanted = tuple(sorted({item.strip().lower() for item in required if item.strip()}))
    declared = tuple(sorted(subject_declared))
    matched = tuple(item for item in wanted if item in declared)
    missing = tuple(item for item in wanted if item not in declared)
    return CapabilityMatch(
        required=wanted,
        matched=matched,
        missing=missing,
        requirements_declared=requirements_declared or bool(wanted),
        subject_declares=bool(declared),
    )


def evaluate_hospital(
    hospital: HospitalProfile, required_capabilities: Iterable[str] = ()
) -> tuple[Verdict, CapabilityMatch]:
    """Hard constraints for a destination hospital.

    A capability requirement that cannot be verified is a rejection, not a pass:
    claiming a hospital is clinically suitable on the basis of data the system
    does not have is exactly the fabrication this layer exists to prevent.
    """
    rejections: list[Rejection] = []
    if hospital.operational_status == "CLOSED":
        rejections.append(Rejection.HOSPITAL_NOT_OPERATIONAL)
    if hospital.capacity_available <= 0:
        rejections.append(Rejection.HOSPITAL_CAPACITY_UNAVAILABLE)

    match = match_capabilities(
        required_capabilities,
        hospital.declared_capabilities,
        requirements_declared=False,
    )
    if match.required:
        if match.missing:
            if match.subject_declares:
                rejections.append(Rejection.HOSPITAL_CAPABILITY_MISMATCH)
            else:
                rejections.append(Rejection.HOSPITAL_CAPABILITY_UNKNOWN)

    return Verdict(not rejections, tuple(rejections)), match


def evaluate_route(route: RouteProfile) -> Verdict:
    """Hard constraints for a persisted route candidate.

    Route status viability is delegated to the Phase 5 resilience engine so
    that "viable" keeps exactly one definition across the codebase; the
    optimizer never re-decides whether a FAILED or DEGRADED route may be used.
    """
    from app.services.route_resilience import is_route_status_viable

    rejections: list[Rejection] = []
    if not is_route_status_viable(route.status):  # type: ignore[arg-type]
        rejections.append(Rejection.ROUTE_STATUS_NON_VIABLE)
    if not route.route_score_viable:
        rejections.append(Rejection.ROUTE_REJECTED_BY_ROUTE_SCORING)
    if route.geometry_points and route.geometry_points < 2:
        rejections.append(Rejection.ROUTE_GEOMETRY_MISSING)
    return Verdict(not rejections, tuple(rejections))


def evaluate_resource(
    resource: ResourceProfile,
    *,
    mission_id: UUID,
    required_vehicle_types: Iterable[str] = (),
    required_capabilities: Iterable[str] = (),
) -> tuple[Verdict, CapabilityMatch]:
    """Hard constraints for a single resource.

    A vehicle already committed to a *different* active mission is rejected
    outright. A vehicle already attached to this same mission is not a
    conflict -- it is the vehicle the mission already has -- but it is still
    subject to every other constraint, and it can only satisfy one requirement
    slot (enforced by :mod:`resource_allocation`, which owns the assignment).
    """
    rejections: list[Rejection] = []
    if resource.status != AVAILABLE_RESOURCE_STATUS:
        rejections.append(Rejection.RESOURCE_UNAVAILABLE)
    if resource.mission_id is not None and resource.mission_id != mission_id:
        rejections.append(Rejection.RESOURCE_COMMITTED_ELSEWHERE)

    wanted_types = {
        item.strip().upper() for item in required_vehicle_types if str(item).strip()
    }
    if wanted_types and resource.vehicle_type.upper() not in wanted_types:
        rejections.append(Rejection.RESOURCE_TYPE_MISMATCH)

    match = match_capabilities(
        required_capabilities,
        resource.declared_capabilities,
        requirements_declared=False,
    )
    if match.required:
        if match.missing:
            if match.subject_declares:
                rejections.append(Rejection.RESOURCE_CAPABILITY_MISMATCH)
            else:
                rejections.append(Rejection.RESOURCE_CAPABILITY_UNKNOWN)

    return Verdict(not rejections, tuple(rejections)), match


@dataclass
class CandidatePool:
    """Constraint-checked inputs, with every rejection retained for reporting."""

    hospitals: list[HospitalProfile] = field(default_factory=list)
    resources: list[ResourceProfile] = field(default_factory=list)
    hospital_rejections: dict[UUID, Verdict] = field(default_factory=dict)
    resource_rejections: dict[UUID, Verdict] = field(default_factory=dict)
    route_rejections: dict[UUID, Verdict] = field(default_factory=dict)

