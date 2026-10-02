import pytest
from sentinel_ai.api import predict_eta, GenerateRoutesRequest, generate_and_score_routes
from sentinel_ai.contracts import PredictEtaRequest
from sentinel_ai.world.scenario import generate_scenario

def test_api_predict_eta():
    state = generate_scenario("TEST", 42, "mixed")
    req = PredictEtaRequest(
        route=["N_0_0->N_1_0"],
        depart_time_utc=state.timestamp_utc,
        world_state=state
    )
    res = predict_eta(req)
    assert res.baseline_eta_min > 0
    assert res.source in ["ml", "baseline_fallback"]

def test_api_generate_routes():
    state = generate_scenario("TEST", 42, "mixed")
    req = GenerateRoutesRequest(
        origin="N_0_0",
        destination="N_5_5",
        depart_time_utc=state.timestamp_utc,
        world_state=state
    )
    res = generate_and_score_routes(req)
    assert len(res.routes) > 0
    roles = [r.role for r in res.routes]
    assert "primary" in roles
