"""Deterministic prediction contract and baseline predictor tests."""

from uuid import UUID

import pytest
from pydantic import ValidationError

from app.schemas.predictions import (
    PredictionKind,
    PredictionResult,
    PredictionStatus,
)
from app.services.predictors import (
    BASELINE_VERSION,
    CongestionPredictor,
    EtaPredictor,
    EventFeature,
    HazardFeature,
    HazardImpactPredictor,
    MissionPredictionContext,
    RouteFailurePredictor,
    RouteFeature,
    VehicleFeature,
)

MISSION_ID = UUID(int=1)
ROUTE_ID = UUID(int=2)
VEHICLE_ID = UUID(int=3)
HAZARD_ID = UUID(int=4)


def context(
    *,
    routes: tuple[RouteFeature, ...] = (),
    vehicles: tuple[VehicleFeature, ...] = (),
    hazards: tuple[HazardFeature, ...] = (),
    events: tuple[EventFeature, ...] = (),
    hazard_event: bool = False,
) -> MissionPredictionContext:
    return MissionPredictionContext(
        mission_id=MISSION_ID,
        mission_status="ACTIVE",
        routes=routes,
        vehicles=vehicles,
        hazards=hazards,
        recent_events=events,
        active_hazard_event_seen=hazard_event,
    )


def route(risk_score: float | None = None) -> RouteFeature:
    return RouteFeature(
        id=ROUTE_ID,
        vehicle_id=VEHICLE_ID,
        distance_meters=1_000,
        estimated_duration_seconds=100,
        risk_score=risk_score,
    )


def test_prediction_contract_accepts_available_result_and_type_enum() -> None:
    result = PredictionResult(
        prediction_type="ETA",
        value={"eta_seconds": 10},
        confidence=0.8,
        horizon_seconds=300,
        factors=[
            {"factor": "route_duration", "contribution": 1, "description": "Known ETA"}
        ],
        model_version=BASELINE_VERSION,
        source="sentinel_prediction_engine",
    )
    assert result.prediction_type is PredictionKind.ETA


def test_prediction_contract_rejects_unknown_type_and_invalid_probabilities() -> None:
    with pytest.raises(ValidationError):
        PredictionResult(
            prediction_type="OTHER",
            value={},
            confidence=0.5,
            horizon_seconds=30,
            model_version=BASELINE_VERSION,
            source="test",
        )
    for field, value in (("probability", 1.1), ("confidence", -0.1)):
        with pytest.raises(ValidationError):
            PredictionResult.model_validate(
                {
                    "prediction_type": "ROUTE_FAILURE",
                    "value": {"risk_level": "LOW"},
                    "confidence": 0.5,
                    "probability": 0.2,
                    "horizon_seconds": 30,
                    "factors": [
                        {
                            "factor": "test_signal",
                            "contribution": 0.1,
                            "description": "Test factor",
                        }
                    ],
                    "model_version": BASELINE_VERSION,
                    "source": "test",
                    field: value,
                }
            )


def test_unavailable_contract_requires_reason_and_missing_inputs() -> None:
    with pytest.raises(ValidationError):
        PredictionResult(
            prediction_type="ETA",
            status=PredictionStatus.UNAVAILABLE,
            confidence=0,
            horizon_seconds=30,
            model_version=BASELINE_VERSION,
            source="test",
        )
    unavailable = PredictionResult(
        prediction_type="ETA",
        status="UNAVAILABLE",
        value=None,
        confidence=0,
        horizon_seconds=30,
        factors=[
            {
                "factor": "missing_inputs",
                "contribution": 0,
                "description": "No route",
            }
        ],
        model_version=BASELINE_VERSION,
        source="test",
        reason="No route",
        missing_inputs=["active_route"],
    )
    assert unavailable.reason == "No route"


def test_eta_uses_route_distance_and_vehicle_speed_without_route_selection() -> None:
    result = EtaPredictor().predict(
        context(
            routes=(route(),),
            vehicles=(VehicleFeature(id=VEHICLE_ID, speed_meters_per_second=10),),
        ),
        300,
    )
    assert result.value == {
        "routes": [
            {
                "route_id": str(ROUTE_ID),
                "vehicle_id": str(VEHICLE_ID),
                "eta_seconds": 100.0,
                "method": "distance_over_vehicle_speed",
            }
        ]
    }
    assert {factor.factor for factor in result.factors} >= {
        "active_route",
        "route_distance",
        "vehicle_speed",
    }


def test_eta_falls_back_to_route_duration() -> None:
    result = EtaPredictor().predict(
        context(
            routes=(route(),),
            vehicles=(VehicleFeature(id=VEHICLE_ID, speed_meters_per_second=None),),
        ),
        300,
    )
    assert result.value is not None
    assert result.value["routes"][0]["eta_seconds"] == 100.0
    assert result.value["routes"][0]["method"] == "route_estimated_duration_fallback"


def test_eta_is_unavailable_without_route_or_usable_route_data() -> None:
    missing_route = EtaPredictor().predict(context(), 300)
    no_estimate = EtaPredictor().predict(
        context(
            routes=(
                RouteFeature(
                    id=ROUTE_ID,
                    vehicle_id=VEHICLE_ID,
                    distance_meters=100,
                    estimated_duration_seconds=0,
                    risk_score=None,
                ),
            ),
            vehicles=(VehicleFeature(id=VEHICLE_ID, speed_meters_per_second=None),),
        ),
        300,
    )
    assert missing_route.status is PredictionStatus.UNAVAILABLE
    assert "active_route" in missing_route.missing_inputs
    assert no_estimate.status is PredictionStatus.UNAVAILABLE


def test_route_failure_is_deterministic_and_route_risk_changes_probability() -> None:
    predictor = RouteFailurePredictor()
    low = predictor.predict(context(routes=(route(0.1),)), 300)
    high = predictor.predict(context(routes=(route(0.9),)), 300)
    repeated = predictor.predict(context(routes=(route(0.9),)), 300)
    assert low.probability < high.probability
    assert high.model_version == "baseline_rule_v1"
    assert high.metadata["not_a_trained_probability"] is True
    assert high.model_dump() == repeated.model_dump()
    assert 0 <= high.probability <= 1
    assert "route_risk_score" in {factor.factor for factor in high.factors}


def test_route_failure_accounts_for_closure_and_deviation_events() -> None:
    predictor = RouteFailurePredictor()
    baseline = predictor.predict(context(routes=(route(0.2),)), 300)
    with_events = predictor.predict(
        context(
            routes=(route(0.2),),
            events=(
                EventFeature("ROAD_CLOSURE", {}),
                EventFeature("ROUTE_DEVIATION", {}),
            ),
        ),
        300,
    )
    assert with_events.probability > baseline.probability
    assert {factor.factor for factor in with_events.factors} >= {
        "road_closure",
        "route_deviation",
    }


def test_route_failure_probability_is_bounded_with_all_risk_signals() -> None:
    result = RouteFailurePredictor().predict(
        context(
            routes=(route(4.0),),
            hazards=(HazardFeature(HAZARD_ID, "FIRE", 5, 100),),
            events=(
                EventFeature("ROAD_CLOSURE", {}),
                EventFeature("ROUTE_DEVIATION", {}),
                EventFeature("CONGESTION_CHANGED", {"level": "HIGH"}),
            ),
        ),
        300,
    )
    assert result.probability == 1.0


def test_congestion_uses_route_relative_vehicle_speed() -> None:
    result = CongestionPredictor().predict(
        context(
            routes=(route(),),
            vehicles=(VehicleFeature(id=VEHICLE_ID, speed_meters_per_second=2),),
        ),
        300,
    )
    assert result.value == {"level": "HIGH", "relative_speed": 0.2}
    assert result.probability == pytest.approx(0.8)
    assert result.confidence > 0


def test_congestion_is_unavailable_without_observable_signals() -> None:
    result = CongestionPredictor().predict(context(), 300)
    assert result.status is PredictionStatus.UNAVAILABLE
    assert "vehicle_speed" in result.missing_inputs


def test_congestion_event_can_supply_an_observed_level() -> None:
    result = CongestionPredictor().predict(
        context(events=(EventFeature("CONGESTION_CHANGED", {"level": "SEVERE"}),)),
        300,
    )
    assert result.value == {"level": "HIGH"}
    assert result.probability == 0.85


def test_hazard_impact_uses_severity_and_route_proximity() -> None:
    result = HazardImpactPredictor().predict(
        context(
            routes=(route(),),
            hazards=(HazardFeature(HAZARD_ID, "FIRE", 5, 0),),
        ),
        300,
    )
    assert result.value is not None
    assert result.value["impact_level"] == "HIGH"
    assert result.probability == 1.0
    assert result.factors[0].factor == "hazard_severity_and_proximity"


def test_hazard_impact_reports_low_when_no_active_hazards_exist() -> None:
    result = HazardImpactPredictor().predict(context(), 300)
    assert result.status is PredictionStatus.AVAILABLE
    assert result.value == {"impact_level": "LOW", "relevant_hazards": []}
    assert result.probability == 0.0


def test_hazard_impact_is_unavailable_when_geometry_or_route_is_missing() -> None:
    predictor = HazardImpactPredictor()
    event_without_geometry = predictor.predict(context(hazard_event=True), 300)
    no_route = predictor.predict(
        context(hazards=(HazardFeature(HAZARD_ID, "FIRE", 3, None),)), 300
    )
    no_distance = predictor.predict(
        context(
            routes=(route(),),
            hazards=(HazardFeature(HAZARD_ID, "FIRE", 3, None),),
        ),
        300,
    )
    assert event_without_geometry.status is PredictionStatus.UNAVAILABLE
    assert "hazard_geometry" in event_without_geometry.missing_inputs
    assert no_route.status is PredictionStatus.UNAVAILABLE
    assert "active_route_geometry" in no_route.missing_inputs
    assert no_distance.status is PredictionStatus.UNAVAILABLE
    assert "hazard_route_distance" in no_distance.missing_inputs