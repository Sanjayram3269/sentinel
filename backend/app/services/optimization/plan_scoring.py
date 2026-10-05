"""The soft objective for mission optimization.

This module ranks options. It never rejects anything -- that is
:mod:`constraints`' job -- and it is only ever handed options that already
passed the hard constraints.

Convention, inherited from Phase 5 route scoring so the two layers read the
same way: every factor is a **cost** normalised into [0, 1] where 1 is worst,
the plan score is ``sum(weight * cost) / sum(available weights)``, and the
*lowest* score wins.

Two rules keep the explanation honest:

* A factor that cannot be computed is ``None``. It is dropped from both the
  numerator and the denominator, and the resulting ``coverage`` is reported, so
  a plan scored on three factors never looks like one scored on six.
* ``coverage == 0`` yields ``total is None``. The optimizer then reports the
  score as unavailable rather than inventing a number for a plan that no
  factor could speak to.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from app.config import Settings


@dataclass(frozen=True)
class PlanScoreWeights:
    """Soft objective weights. A zero weight removes a factor from the sum."""

    eta: float
    route_risk: float
    capability: float
    capacity: float
    reliability: float
    resource_proximity: float

    @classmethod
    def from_settings(cls, settings: Settings) -> "PlanScoreWeights":
        return cls(
            eta=settings.optimization_weight_eta,
            route_risk=settings.optimization_weight_route_risk,
            capability=settings.optimization_weight_capability,
            capacity=settings.optimization_weight_capacity,
            reliability=settings.optimization_weight_reliability,
            resource_proximity=settings.optimization_weight_resource_proximity,
        )

    def as_dict(self) -> dict[str, float]:
        return {
            "eta": self.eta,
            "route_risk": self.route_risk,
            "capability": self.capability,
            "capacity": self.capacity,
            "reliability": self.reliability,
            "resource_proximity": self.resource_proximity,
        }


@dataclass(frozen=True)
class PlanFactors:
    """Raw, pre-normalisation inputs for one hospital + route combination."""

    eta_seconds: int
    capability_matched: int
    capability_required: int
    capacity_available: int
    capacity_total: int = 0
    route_risk: float | None = None
    reliability: float | None = None
    resource_response_meters: float | None = None

    @property
    def capability_shortfall(self) -> float:
        """Fraction of required capabilities the hospital does not declare.

        Zero when nothing was required, so a mission with no stated capability
        requirement contributes no artificial shortfall to either side.
        """
        if self.capability_required <= 0:
            return 0.0
        return 1.0 - (self.capability_matched / self.capability_required)


@dataclass(frozen=True)
class PlanFactorCosts:
    """Normalised per-factor costs in [0, 1]. None means "unavailable"."""

    eta: float
    route_risk: float | None
    capability: float
    capacity: float
    reliability: float | None
    resource_proximity: float | None

    def items(self) -> tuple[tuple[str, float | None], ...]:
        return (
            ("eta", self.eta),
            ("route_risk", self.route_risk),
            ("capability", self.capability),
            ("capacity", self.capacity),
            ("reliability", self.reliability),
            ("resource_proximity", self.resource_proximity),
        )


@dataclass(frozen=True)
class PlanScore:
    """Total cost plus the evidence needed to explain it."""

    total: float | None
    coverage: float
    costs: PlanFactorCosts
    contributions: dict[str, float]
    unavailable_factors: tuple[str, ...]


def _capacity_cost(factors: PlanFactors) -> float | None:
    """Share of capacity already taken; None when the hospital reports no total."""
    if factors.capacity_total <= 0:
        return None
    free = max(0.0, min(1.0, factors.capacity_available / factors.capacity_total))
    return 1.0 - free


@dataclass(frozen=True)
class PlanOption:
    """A hospital + route pair: the unit of decision.

    SENTINEL does not rank hospitals and routes independently and then combine
    the winners. The pair *is* the candidate, because a hospital's usefulness is
    inseparable from how long the current road network takes to reach it.
    """

    hospital: Any
    route: Any
    capability: Any
    factors: PlanFactors
    score: PlanScore

    @property
    def hospital_id(self) -> Any:
        return self.hospital.hospital_id

    @property
    def route_id(self) -> Any:
        return self.route.route_id

    def ordering_key(self) -> tuple[float, int, int, str, str]:
        """Total, then resilient role preference, then stable identity.

        Every component after ``total`` exists to make the choice deterministic
        when two options score identically. Role preference is deliberate and
        ties to Phase 5: when a primary and a backup are equally good, the
        plan that already has a resilience structure is preferred.
        """
        total = self.score.total if self.score.total is not None else float("inf")
        return (
            total,
            self.route.role_order,
            self.route.route_rank,
            str(self.hospital_id),
            str(self.route_id),
        )


class PlanScoringEngine:
    """Weighted-sum scorer over constraint-feasible options."""

    def __init__(self, weights: PlanScoreWeights) -> None:
        self.weights = weights

    # -- normalisation -------------------------------------------------

    @staticmethod
    def _normalize(values: Sequence[float | None]) -> list[float | None]:
        """Min-max normalise so the best observed value maps to 0.

        Matches the Phase 5 convention: a single sample, or a set where every
        value is identical, normalises to 0.5 rather than 0 or 1, because the
        factor genuinely carries no discriminating information there.
        """
        known = [value for value in values if value is not None]
        if not known:
            return [None for _ in values]
        low, high = min(known), max(known)
        if len(values) <= 1 or high == low:
            return [0.5 if value is not None else None for value in values]
        return [None if value is None else (value - low) / (high - low) for value in values]

    def normalize(self, factors: Sequence[PlanFactors]) -> list[PlanFactorCosts]:
        """Normalise a whole option set so costs are comparable across options.

        Normalisation is set-relative by design: it is what lets a farther
        hospital with more free beds and higher reliability beat the nearest
        one. Absolute factors need no set-relative treatment:

        * ``capacity`` is the *share* of capacity that is free. Comparing raw
          bed counts across hospitals would rank a large hospital above a small
          one purely on size, and normalising the counts within the option set
          would instead force the worst hospital to a full 1.0 penalty no
          matter how good it actually is. The share is absolute and comparable.
        * ``reliability``, ``capability`` and ``route_risk`` are already
          absolute quantities in [0, 1].
        * ``eta`` is set-relative because there is no defensible absolute
          "good ETA" for an emergency; only the alternatives make it meaningful.
        """
        etas = self._normalize([float(item.eta_seconds) for item in factors])
        proximities = self._normalize(
            [item.resource_response_meters for item in factors]
        )
        costs: list[PlanFactorCosts] = []
        for index, item in enumerate(factors):
            assert etas[index] is not None
            costs.append(
                PlanFactorCosts(
                    eta=etas[index],
                    route_risk=item.route_risk,
                    capability=item.capability_shortfall,
                    capacity=_capacity_cost(item),
                    reliability=(
                        None
                        if item.reliability is None
                        else max(0.0, min(1.0, 1.0 - item.reliability))
                    ),
                    resource_proximity=proximities[index],
                )
            )
        return costs

    # -- scoring -------------------------------------------------------

    def score(self, costs: PlanFactorCosts) -> PlanScore:
        weights = self.weights.as_dict()
        total_weight = sum(weights.values())
        available = [
            (name, weights[name], value)
            for name, value in costs.items()
            if value is not None
        ]
        available_weight = sum(weight for _, weight, _ in available)
        if available_weight <= 0:
            return PlanScore(
                total=None,
                coverage=0.0,
                costs=costs,
                contributions={},
                unavailable_factors=tuple(name for name, _ in costs.items()),
            )
        contributions = {
            name: weight * value for name, weight, value in available
        }
        return PlanScore(
            total=sum(contributions.values()) / available_weight,
            coverage=available_weight / total_weight if total_weight else 0.0,
            costs=costs,
            contributions=contributions,
            unavailable_factors=tuple(
                name for name, value in costs.items() if value is None
            ),
        )
