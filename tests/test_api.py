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

def test_missing_model_dir_fallback(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SENTINEL_MODEL_DIR", "/nonexistent")
    import sentinel_ai.prediction.inference as inf
    inf._MODELS = None
    
    from sentinel_ai.world.city_graph import build_city_graph
    from sentinel_ai.contracts import WorldState, PredictEtaRequest
    G = build_city_graph(seed=42)
    req = PredictEtaRequest(
        route=["N_0_0->N_0_1"],
        depart_time_utc=0.0,
        world_state=WorldState(timestamp_utc=0.0, incidents=[], units=[], hospitals=[], hazards=[], closed_edges=[], demand_level=1.0)
    )
    from sentinel_ai.api import predict_eta
    res = predict_eta(req, G=G)
    assert res.source == "baseline_fallback"
    assert "missing or invalid" in res.reasons[0]
