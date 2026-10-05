"""Controlled vocabularies stored by the SENTINEL domain."""

from enum import Enum


class MissionStatus(str, Enum):
    CREATED = "CREATED"
    DISPATCHED = "DISPATCHED"
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class IncidentType(str, Enum):
    MEDICAL = "MEDICAL"
    FIRE = "FIRE"
    ACCIDENT = "ACCIDENT"
    FLOOD = "FLOOD"
    EARTHQUAKE = "EARTHQUAKE"
    CYCLONE = "CYCLONE"
    HAZMAT = "HAZMAT"
    OTHER = "OTHER"


class VehicleType(str, Enum):
    AMBULANCE = "AMBULANCE"
    FIRE_TRUCK = "FIRE_TRUCK"
    POLICE = "POLICE"
    RESCUE = "RESCUE"
    OTHER = "OTHER"


class VehicleStatus(str, Enum):
    AVAILABLE = "AVAILABLE"
    EN_ROUTE = "EN_ROUTE"
    AT_SCENE = "AT_SCENE"
    TRANSPORTING = "TRANSPORTING"
    OFFLINE = "OFFLINE"


class RouteStatus(str, Enum):
    CANDIDATE = "CANDIDATE"
    ACTIVE = "ACTIVE"
    DEGRADED = "DEGRADED"
    FAILED = "FAILED"
    COMPLETED = "COMPLETED"
    ABORTED = "ABORTED"
    BACKUP = "BACKUP"
    CONTINGENCY = "CONTINGENCY"
    BLOCKED = "BLOCKED"
    REJECTED = "REJECTED"


class ResilienceRole(str, Enum):
    PRIMARY = "PRIMARY"
    BACKUP = "BACKUP"
    CONTINGENCY = "CONTINGENCY"


class ApprovalStatus(str, Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    MODIFIED = "MODIFIED"


class HazardType(str, Enum):
    FLOOD = "FLOOD"
    FIRE = "FIRE"
    EARTHQUAKE = "EARTHQUAKE"
    CYCLONE = "CYCLONE"
    ROAD_CLOSURE = "ROAD_CLOSURE"
    ACCIDENT = "ACCIDENT"
    OTHER = "OTHER"


class HazardStatus(str, Enum):
    ACTIVE = "ACTIVE"
    RESOLVED = "RESOLVED"
    EXPIRED = "EXPIRED"


class PredictionType(str, Enum):
    ETA = "ETA"
    CONGESTION = "CONGESTION"
    ROUTE_FAILURE = "ROUTE_FAILURE"
    ROUTE_RISK = "ROUTE_RISK"
    HAZARD_IMPACT = "HAZARD_IMPACT"
    OTHER = "OTHER"


class OperationalStatus(str, Enum):
    OPERATIONAL = "OPERATIONAL"
    LIMITED = "LIMITED"
    CLOSED = "CLOSED"


class PlanStatus(str, Enum):
    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    SUPERSEDED = "SUPERSEDED"
    # Phase 8 human-in-the-loop states. The optimizer produces DRAFT; a
    # computed plan is offered for review as READY_FOR_REVIEW; a human decision
    # moves it to APPROVED or REJECTED; EXECUTION_AUTHORIZED is granted by the
    # server-side gate rather than by the approval itself, which is the
    # distinction that stops a simulation result from becoming an action.
    READY_FOR_REVIEW = "READY_FOR_REVIEW"
    EXECUTION_AUTHORIZED = "EXECUTION_AUTHORIZED"
    EXECUTING = "EXECUTING"
    REPLAN_REQUIRED = "REPLAN_REQUIRED"
    # Deliberately no COMPLETED state. Execution is prototype-only: there is
    # no real mission that finishes, so a "completed" plan would be a state
    # nothing could ever honestly enter.


class SimulationStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


def enum_values(enum_type: type[Enum]) -> list[str]:
    """Return explicit values so PostgreSQL enum labels match the API contract."""
    return [member.value for member in enum_type]