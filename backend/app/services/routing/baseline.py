"""Development adapter for externally or fixture-supplied route proposals."""

from app.schemas.routing import RouteProposal
from app.services.routing.base import RoutingRequest


class BaselineDevelopmentProvider:
    """Normalize supplied routes without fabricating a road network."""

    name = "baseline_development_provider"

    def calculate_routes(self, request: RoutingRequest) -> list[RouteProposal]:
        return [proposal.model_copy(deep=True) for proposal in request.proposals]