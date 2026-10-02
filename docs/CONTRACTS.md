# SENTINEL API Contracts

## 1. Predict ETA
**Function:** `predict_eta(req: PredictEtaRequest, *, G: Any = None) -> PredictEtaResponse`
**Request Example:**
```json
{
  "route": ["N_0_0->N_1_0"],
  "depart_time_utc": 1672531200.0,
  "world_state": {
    "timestamp_utc": 1672531200.0,
    "demand_level": 1.0,
    "closed_edges": [],
    "incidents": [],
    "hazards": [],
    "units": [],
    "hospitals": []
  }
}
```
**Response Example:**
```json
{
  "contract_version": "1.0.0",
  "model_version": "1.0.0",
  "source": "ml",
  "confidence": 0.9,
  "reasons": [],
  "evidence": {},
  "eta_p10_min": 1.1,
  "eta_p50_min": 1.5,
  "eta_p90_min": 2.1,
  "baseline_eta_min": 1.3
}
```

## 2. Generate and Score Routes
**Function:** `generate_and_score_routes(req: GenerateRoutesRequest, *, G: Any = None) -> GenerateRoutesResponse`
**Request Example:**
```json
{
  "origin": "N_0_0",
  "destination": "N_5_5",
  "depart_time_utc": 1672531200.0,
  "world_state": {...}
}
```
**Response Example:**
```json
{
  "contract_version": "1.0.0",
  "model_version": "1.0.0",
  "source": "ml",
  "confidence": 0.9,
  "reasons": [],
  "evidence": {},
  "routes": [
    {
      "route_id": "R_0",
      "edges": ["N_0_0->N_1_0", "N_1_0->N_2_0"],
      "eta_p10_min": 3.0,
      "eta_p50_min": 3.5,
      "eta_p90_min": 4.5,
      "risk_probability": 0.1,
      "score": 4.2,
      "role": "primary",
      "role_reason": "Best score"
    }
  ]
}
```

## 3. Compute Resilience
**Function:** `compute_resilience(req: ComputeResilienceRequest, *, G: Any = None) -> ComputeResilienceResponse`
**Request Example:**
```json
{
  "routes": [...],
  "world_state": {...},
  "corridor_readiness": 0.5
}
```
**Response Example:**
```json
{
  "contract_version": "1.0.0",
  "model_version": "1.0.0",
  "source": "ml",
  "confidence": 0.9,
  "reasons": ["Route redundancy is 80% based on available diverse paths."],
  "evidence": {},
  "resilience_score": 85.0,
  "components": {
    "route_redundancy": 80.0,
    "backup_viability": 100.0,
    "failure_probability": 90.0,
    "hazard_exposure": 100.0,
    "destination_availability": 100.0,
    "resource_availability": 100.0,
    "corridor_readiness": 50.0
  }
}
```

## 4. Rank Destinations
**Function:** `rank_destinations(req: RankDestinationsRequest, *, G: Any = None) -> RankDestinationsResponse`
**Request Example:**
```json
{
  "origin": "N_5_5",
  "depart_time_utc": 1672531200.0,
  "world_state": {...},
  "required_capabilities": ["trauma"]
}
```
**Response Example:**
```json
{
  "contract_version": "1.0.0",
  "model_version": "1.0.0",
  "source": "ml",
  "confidence": 0.9,
  "reasons": [],
  "evidence": {},
  "rankings": [
    {
      "hospital_id": "H1",
      "rank": 1,
      "suitable": true,
      "unsuitable_reasons": [],
      "predicted_eta_min": 5.2,
      "route_reliability_score": 0.95
    }
  ]
}
```

## 5. Optimize Mission
**Function:** `optimize_mission(req: OptimizeMissionRequest, *, G: Any = None) -> OptimizeMissionResponse`
**Request Example:**
```json
{
  "incident_id": "INC_DEMO_1",
  "world_state": {...},
  "available_unit_ids": ["U1", "U2"]
}
```
**Response Example:**
```json
{
  "contract_version": "1.0.0",
  "model_version": "1.0.0",
  "source": "ml",
  "confidence": 0.9,
  "reasons": ["CP-SAT optimizer found a plan.", "Solver status: OPTIMAL"],
  "evidence": {"solve_time_s": 0.05},
  "assignments": [
    {
      "unit_id": "U1",
      "destination_node": "N_5_5",
      "route_edges": ["N_0_0->N_1_0"],
      "expected_eta_min": 8.5,
      "is_hospital": false
    }
  ],
  "expected_mission_time_min": 8.5,
  "alternatives": []
}
```

## 6. Simulate Counterfactual
**Function:** `simulate_counterfactual(req: SimulateCounterfactualRequest, *, G: Any = None) -> SimulateCounterfactualResponse`
**Request Example:**
```json
{
  "world_state": {...},
  "mission_request": {...},
  "change": {
    "change_type": "close_edge",
    "change_data": {"edge_id": "N_1_1->N_1_2"}
  }
}
```
**Response Example:**
```json
{
  "contract_version": "1.0.0",
  "model_version": "1.0.0",
  "source": "ml",
  "confidence": 0.9,
  "reasons": ["Counterfactual simulated successfully."],
  "evidence": {},
  "new_plan": {...},
  "delta_eta_min": 2.5,
  "route_changed": true,
  "delta_resilience": -5.0
}
```
