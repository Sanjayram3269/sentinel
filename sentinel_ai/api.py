from typing import List, Dict, Any

from sentinel_ai.contracts import (
    PredictEtaRequest, PredictEtaResponse,
    PredictRouteRiskRequest, PredictRouteRiskResponse,
    GenerateRoutesRequest, GenerateRoutesResponse,
    ComputeResilienceRequest, ComputeResilienceResponse,
    RankDestinationsRequest, RankDestinationsResponse,
    OptimizeMissionRequest, OptimizeMissionResponse,
    SimulateCounterfactualRequest, SimulateCounterfactualResponse
)
from sentinel_ai.world.city_graph import build_city_graph
from sentinel_ai.prediction.inference import predict_eta as do_predict_eta, predict_risk as do_predict_risk
from sentinel_ai.routing.candidates import generate_and_score_routes as do_generate_routes
from sentinel_ai.routing.resilience import compute_resilience as do_compute_resilience
from sentinel_ai.hospital.ranking import rank_destinations as do_rank_destinations
from sentinel_ai.optimization.optimizer import optimize_mission as do_optimize_mission
from sentinel_ai.whatif.counterfactual import simulate_counterfactual as do_simulate_counterfactual

_G = None

def _get_G(G):
    global _G
    if G is not None: return G
    if _G is None:
        from sentinel_ai.world.city_graph import build_city_graph
        _G = build_city_graph(seed=42)
    return _G

def predict_eta(req: PredictEtaRequest, *, G: Any = None) -> PredictEtaResponse:
    p10, p50, p90, baseline, src, conf = do_predict_eta(_get_G(G), req.route, req.depart_time_utc, req.world_state)
    reasons = []
    if "missing or invalid" in src:
        reasons.append(src)
        src = "baseline_fallback"
    return PredictEtaResponse(
        source=src,
        confidence=conf,
        eta_p10_min=p10,
        eta_p50_min=p50,
        eta_p90_min=p90,
        baseline_eta_min=baseline,
        reasons=reasons
    )

def predict_route_risk(req: PredictRouteRiskRequest, *, G: Any = None) -> PredictRouteRiskResponse:
    fails, prob, src, conf = do_predict_risk(_get_G(G), req.route, req.depart_time_utc, req.world_state)
    reasons = []
    if "missing or invalid" in src:
        reasons.append(src)
        src = "baseline_fallback"
    return PredictRouteRiskResponse(
        source=src,
        confidence=conf,
        route_fails=fails,
        failure_probability=prob,
        reasons=reasons
    )

def generate_and_score_routes(req: GenerateRoutesRequest, *, G: Any = None) -> GenerateRoutesResponse:
    routes = do_generate_routes(_get_G(G), req.origin, req.destination, req.depart_time_utc, req.world_state)
    return GenerateRoutesResponse(
        source="ml",
        confidence=0.9,
        routes=routes
    )

def compute_resilience(req: ComputeResilienceRequest, *, G: Any = None) -> ComputeResilienceResponse:
    score, comps, reasons = do_compute_resilience(req.routes, req.world_state, req.corridor_readiness) # do_compute_resilience doesn't take G
    return ComputeResilienceResponse(
        source="ml",
        confidence=0.9,
        reasons=reasons,
        resilience_score=score,
        components=comps
    )

def rank_destinations(req: RankDestinationsRequest, *, G: Any = None) -> RankDestinationsResponse:
    rankings = do_rank_destinations(_get_G(G), req.origin, req.depart_time_utc, req.world_state, req.required_capabilities)
    return RankDestinationsResponse(
        source="ml",
        confidence=0.9,
        rankings=rankings
    )

def optimize_mission(req: OptimizeMissionRequest, *, G: Any = None) -> OptimizeMissionResponse:
    return do_optimize_mission(_get_G(G), req.incident_id, req.world_state, req.available_unit_ids)

def simulate_counterfactual(req: SimulateCounterfactualRequest, *, G: Any = None) -> SimulateCounterfactualResponse:
    current_plan = optimize_mission(req.mission_request, G=G)
    current_eta = current_plan.expected_mission_time_min
    
    current_resilience = 0.0
    if current_plan.assignments:
        u_id = current_plan.assignments[0].unit_id
        incident = next((i for i in req.world_state.incidents if i.incident_id == req.mission_request.incident_id), None)
        u = next((unit for unit in req.world_state.units if unit.unit_id == u_id), None)
        if incident and u:
            routes = do_generate_routes(_get_G(G), u.position, incident.node, req.world_state.timestamp_utc, req.world_state)
            current_resilience, _, _ = do_compute_resilience(routes, req.world_state)
            
    return do_simulate_counterfactual(_get_G(G), req.world_state, req.mission_request, req.change, current_eta, current_resilience)
