from typing import List, Dict, Any, Optional, Literal, Tuple
from pydantic import BaseModel, Field
import datetime

CONTRACT_VERSION = "1.0.0"
MODEL_VERSION = "1.0.0"

class BaseResponse(BaseModel):
    contract_version: str = CONTRACT_VERSION
    model_version: str = MODEL_VERSION
    source: Literal["ml", "baseline_fallback"]
    confidence: float
    reasons: List[str] = Field(default_factory=list)
    evidence: Dict[str, Any] = Field(default_factory=dict)

# --- World State & Entities ---

class UnitState(BaseModel):
    unit_id: str
    unit_type: str # ambulance, fire, police
    position: str # node id
    available: bool

class HospitalState(BaseModel):
    hospital_id: str
    node: str
    capacity: int
    available: bool
    capabilities: List[str] # e.g., ICU, trauma

class IncidentState(BaseModel):
    incident_id: str
    node: str
    priority: int
    required_responder_types: List[str]

class HazardState(BaseModel):
    hazard_id: str
    center_node: str
    radius_m: float
    expansion_rate_m_per_s: float

class WorldState(BaseModel):
    timestamp_utc: float
    demand_level: float
    closed_edges: List[str]
    incidents: List[IncidentState]
    hazards: List[HazardState]
    units: List[UnitState]
    hospitals: List[HospitalState]

# --- API Request / Response Models ---

class PredictEtaRequest(BaseModel):
    route: List[str] # List of edge IDs
    depart_time_utc: float
    world_state: WorldState

class PredictEtaResponse(BaseResponse):
    eta_p10_min: float
    eta_p50_min: float
    eta_p90_min: float
    baseline_eta_min: float

class PredictRouteRiskRequest(BaseModel):
    route: List[str]
    depart_time_utc: float
    world_state: WorldState

class PredictRouteRiskResponse(BaseResponse):
    route_fails: bool
    failure_probability: float

class RouteCandidate(BaseModel):
    route_id: str
    edges: List[str]
    eta_p10_min: float
    eta_p50_min: float
    eta_p90_min: float
    risk_probability: float
    score: float
    role: Optional[Literal["primary", "backup", "contingency"]] = None
    role_reason: Optional[str] = None

class GenerateRoutesRequest(BaseModel):
    origin: str
    destination: str
    depart_time_utc: float
    world_state: WorldState

class GenerateRoutesResponse(BaseResponse):
    routes: List[RouteCandidate]

class ComputeResilienceRequest(BaseModel):
    routes: List[RouteCandidate]
    world_state: WorldState
    corridor_readiness: float = 0.5

class ComputeResilienceResponse(BaseResponse):
    resilience_score: float # 0-100
    components: Dict[str, float]

class HospitalRanking(BaseModel):
    hospital_id: str
    rank: Optional[int]
    suitable: bool
    unsuitable_reasons: List[str]
    predicted_eta_min: Optional[float]
    route_reliability_score: Optional[float]

class RankDestinationsRequest(BaseModel):
    origin: str
    depart_time_utc: float
    world_state: WorldState
    required_capabilities: List[str]

class RankDestinationsResponse(BaseResponse):
    rankings: List[HospitalRanking]

class Assignment(BaseModel):
    unit_id: str
    destination_node: str
    route_edges: List[str]
    expected_eta_min: float
    is_hospital: bool

class OptimizeMissionRequest(BaseModel):
    incident_id: str
    world_state: WorldState
    available_unit_ids: List[str]

class OptimizeMissionResponse(BaseResponse):
    assignments: List[Assignment]
    expected_mission_time_min: float
    alternatives: List[Dict[str, Any]] = Field(default_factory=list)

class CounterfactualChange(BaseModel):
    change_type: Literal["close_edge", "disable_hospital", "increase_demand", "add_hazard"]
    change_data: Dict[str, Any]

class SimulateCounterfactualRequest(BaseModel):
    world_state: WorldState
    mission_request: OptimizeMissionRequest
    change: CounterfactualChange

class SimulateCounterfactualResponse(BaseResponse):
    new_plan: OptimizeMissionResponse
    delta_eta_min: float
    route_changed: bool
    delta_resilience: float
