"""Routing-provider abstractions independent from any external engine."""

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from app.schemas.domain import GeoPoint
from app.schemas.routing import RouteProposal


@dataclass(frozen=True)
class RoutingRequest:
    mission_id: UUID
    vehicle_id: UUID
    origin: GeoPoint
    destination: GeoPoint
    proposals: tuple[RouteProposal, ...]
    parameters: dict[str, object]


class RoutingProvider(Protocol):
    name: str

    def calculate_routes(self, request: RoutingRequest) -> list[RouteProposal]: ...