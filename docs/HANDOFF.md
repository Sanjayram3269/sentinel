# SENTINEL AI - Handoff Documentation

Welcome to the AI/Optimization module of SENTINEL (Geoagentic Framework to Support Emergency Movement). This module operates completely decoupled from the FastAPI backend and SUMO simulation, exposing a pure data-in/data-out functional facade.

## 1. Installation

This module is designed to run in a standalone Python environment. 

```bash
# Create and activate a clean virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install the exact frozen dependencies
pip install -r requirements-ai.txt
```

## 2. Environment Variables

- `SENTINEL_MODEL_DIR`: Specifies the directory containing the pickled ML models (`sentinel_models.pkl`). If omitted, it gracefully falls back to `"models"`.
- `OMP_NUM_THREADS`: Must be set to `"1"` for stable performance with XGBoost inside the simulator/optimizer loop.

## 3. The Public Facade (sentinel_ai/api.py)

All entry points are purely functional and accept complete state objects. For testing, the graph `G` can be injected explicitly via keyword argument `G`. If omitted, it will lazy-load the default synthetic city.

### `predict_eta(req: PredictEtaRequest, *, G=None)`
Predicts p10, p50, and p90 travel times for a given route.
```python
from sentinel_ai.api import predict_eta
from sentinel_ai.contracts import PredictEtaRequest, WorldState

req = PredictEtaRequest(
    route_edges=["N_0_0->N_0_1", "N_0_1->N_0_2"],
    depart_time_utc=1672531200.0,
    world_state=WorldState(...)
)
res = predict_eta(req)
print(res.eta_p50_min)
```

### `predict_route_risk(req: PredictRouteRiskRequest, *, G=None)`
Returns the probability of route failure (excessive congestion or deadlock).
```python
from sentinel_ai.api import predict_route_risk
from sentinel_ai.contracts import PredictRouteRiskRequest, WorldState

req = PredictRouteRiskRequest(
    route_edges=["N_0_0->N_0_1", "N_0_1->N_0_2"],
    depart_time_utc=1672531200.0,
    world_state=WorldState(...)
)
res = predict_route_risk(req)
print(f"Failure Probability: {res.failure_probability}")
```

### `generate_and_score_routes(req: GenerateRoutesRequest, *, G=None)`
Generates K-shortest path diverse routes (primary, backup, contingency) to an incident.
```python
from sentinel_ai.api import generate_and_score_routes
from sentinel_ai.contracts import GenerateRoutesRequest, WorldState

req = GenerateRoutesRequest(
    origin="N_0_0",
    destination="N_5_5",
    depart_time_utc=1672531200.0,
    world_state=WorldState(...)
)
res = generate_and_score_routes(req)
for r in res.routes:
    print(r.role, r.eta_p50_min)
```

### `compute_resilience(req: ComputeResilienceRequest, *, G=None)`
Evaluates the redundancy and safety of generated routes against hazards and closures.
```python
from sentinel_ai.api import compute_resilience
from sentinel_ai.contracts import ComputeResilienceRequest, WorldState

req = ComputeResilienceRequest(
    routes=routes_res.routes,
    world_state=WorldState(...)
)
res = compute_resilience(req)
print(f"Resilience Score: {res.resilience_score}/100")
```

### `rank_destinations(req: RankDestinationsRequest, *, G=None)`
Filters hospitals by capacity/capability and ranks them by expected travel time.
```python
from sentinel_ai.api import rank_destinations
from sentinel_ai.contracts import RankDestinationsRequest, WorldState

req = RankDestinationsRequest(
    origin="N_5_5",
    depart_time_utc=1672531200.0,
    world_state=WorldState(...),
    required_capabilities=["trauma"]
)
res = rank_destinations(req)
print("Best Hospital:", res.rankings[0].hospital_id)
```

### `optimize_mission(req: OptimizeMissionRequest, *, G=None)`
Uses OR-Tools CP-SAT to select the best responder units for an incident while minimizing total end-to-end mission time (including hospital transits).
```python
from sentinel_ai.api import optimize_mission
from sentinel_ai.contracts import OptimizeMissionRequest, WorldState

req = OptimizeMissionRequest(
    incident_id="INC_1",
    world_state=WorldState(...),
    available_unit_ids=["U1", "U2"]
)
plan = optimize_mission(req)
print("Expected Mission Time:", plan.expected_mission_time_min)
```

### `simulate_counterfactual(req: SimulateCounterfactualRequest, *, G=None)`
Answers "What-If" queries by temporarily modifying the world state and re-running the CP-SAT optimizer.
```python
from sentinel_ai.api import simulate_counterfactual
from sentinel_ai.contracts import SimulateCounterfactualRequest, CounterfactualChange, WorldState

req = SimulateCounterfactualRequest(
    world_state=WorldState(...),
    mission_request=opt_req, # from optimize_mission
    change=CounterfactualChange(change_type="close_edge", change_data={"edge_id": "N_0_0->N_0_1"})
)
res = simulate_counterfactual(req)
print("ETA Impact:", res.delta_eta_min)
```

### `replan(req: ReplanRequest, *, G=None)`
Wrapper for `simulate_counterfactual` that also measures controller latency.
```python
from sentinel_ai.api import replan
from sentinel_ai.contracts import ReplanRequest, CounterfactualChange, WorldState

req = ReplanRequest(
    world_state=WorldState(...),
    mission_request=opt_req,
    trigger=CounterfactualChange(change_type="close_edge", change_data={"edge_id": "N_0_0->N_0_1"})
)
res = replan(req)
print("New ETA:", res.new_plan.expected_mission_time_min)
```

## 4. Known Limitations

1. **Prediction Fallback**: The predictive models (`use_prediction=True`) systematically over-estimate synthetic grid ETAs due to scalar mismatches between the synthetic network distances and realistic speed bounds. Currently, `use_prediction` defaults to `False`, allowing the system to use the Baseline B exact-simulator shortest path routing loop while retaining full access to mission-level CP-SAT unit assignment.
2. **SUMO Data Unimplemented**: `load_dataset("sumo")` currently raises a `NotImplementedError`. The data generation loop is entirely reliant on the synthetic graph environment.
3. **CP-SAT Mission Overhead**: `optimize_mission` evaluates the entire route chain (`Unit -> Incident -> Hospital`), while Baseline B strictly evaluates `Unit -> Incident`. This structural mismatch causes Sentinel's reported mission times to appear artificially higher during direct closed-loop comparisons.
4. **Infinite Loops in Simulator**: If an ambulance is fully surrounded by impassable hazards/closures in a generated synthetic city, CP-SAT and the simulator routing fallback may struggle to resolve the route. The progress guard (every 5 stalled iterations) forces a fallback mechanism to prevent thread hanging.
