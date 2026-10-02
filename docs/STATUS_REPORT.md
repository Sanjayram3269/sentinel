# SENTINEL AI: Status Report

## 1. REPO SNAPSHOT

**Directory Tree (Depth 3):**
```text
.
├── Makefile
├── README.md
├── config
│   └── default.yaml
├── docs
│   └── CONTRACTS.md
├── models
│   └── sentinel_models.pkl
├── sentinel_ai
│   ├── api.py
│   ├── config_loader.py
│   ├── contracts.py
│   ├── data
│   │   └── loader.py
│   ├── demo
│   │   └── demo_flagship.py
│   ├── experiments
│   │   ├── mission_experiments.py
│   │   ├── output
│   │   └── run_experiments.py
│   ├── hospital
│   │   └── ranking.py
│   ├── optimization
│   │   └── optimizer.py
│   ├── prediction
│   │   ├── baselines.py
│   │   ├── calibration.py
│   │   ├── evaluate_robustness.py
│   │   ├── features.py
│   │   ├── inference.py
│   │   └── train.py
│   ├── routing
│   │   ├── candidates.py
│   │   └── resilience.py
│   ├── whatif
│   │   └── counterfactual.py
│   └── world
│       ├── city_graph.py
│       ├── scenario.py
│       └── simulator.py
└── tests
    ├── test_api.py
    ├── test_components.py
    ├── test_leakage.py
    └── test_world.py
```

**Git Branch & Commits:**
UNKNOWN (Directory is not initialized as a git repository).

**Python Details:**
- **Python Version:** 3.14.6
- **Dependencies:** UNKNOWN (No `requirements.txt`, `Pipfile`, or `pyproject.toml` found in root).
- **Make Targets:** `test`, `train`, `demo`, `experiments`

---

## 2. PUBLIC FACADE (`sentinel_ai/api.py`)

- **`predict_eta(req: PredictEtaRequest) -> PredictEtaResponse`**
  *Status:* Real. Predicts p10, p50, p90 ETAs for a route using XGBoost models, falling back to current-speed baseline if OOD.
- **`predict_route_risk(req: PredictRouteRiskRequest) -> PredictRouteRiskResponse`**
  *Status:* Real. Predicts if a route will fail using an XGBoost classifier.
- **`generate_and_score_routes(req: GenerateRoutesRequest) -> GenerateRoutesResponse`**
  *Status:* Real. Generates top routes bypassing closed edges, scores them using ETA/Risk/Hazard weights, and assigns roles.
- **`compute_resilience(req: ComputeResilienceRequest) -> ComputeResilienceResponse`**
  *Status:* Partial. Returns a deterministic resilience score based on route redundancy and probability of failure.
- **`rank_destinations(req: RankDestinationsRequest) -> RankDestinationsResponse`**
  *Status:* Real. Ranks available hospitals based on remaining capacity and capabilities.
- **`optimize_mission(req: OptimizeMissionRequest) -> OptimizeMissionResponse`**
  *Status:* Real. Uses Google OR-Tools CP-SAT to assign available units to required incident roles, minimizing max travel time.
- **`simulate_counterfactual(req: SimulateCounterfactualRequest) -> SimulateCounterfactualResponse`**
  *Status:* Real. Modifies the world state with an event and runs CP-SAT optimizer to compare ETA/resilience changes.

---

## 3. WORLD AND SIMULATOR

**Edge Travel-Time Formula:**
```python
    volume = 500 * world_state.demand_level * time_of_day_multiplier(current_time)
    travel_time = free_flow_s * (1 + BPR_ALPHA * (volume / capacity) ** BPR_BETA)
    if has_signal:
        rng = np.random.RandomState(seed + int(current_time) % 1000)
        travel_time += rng.uniform(15, 45)
    rng = np.random.RandomState(seed + int(travel_time * 10))
    travel_time *= rng.uniform(0.9, 1.1)
    travel_time = max(free_flow_s * 0.8, travel_time * 0.7)
    return travel_time, False
```

- **Emergency Vehicle Movement:** Moves discretely, advancing time strictly by traversal cost (`current_time += tt_s`). Speed bonus is explicitly capped (`max(free_flow_s * 0.8, travel_time * 0.7)`). Signal delays and noise are pseudo-randomly seeded.
- **Hazard Semantics:** 
```python
        time_elapsed = current_time - world_state.timestamp_utc
        current_radius = hazard.radius_m + max(0, time_elapsed) * hazard.expansion_rate_m_per_s
        if dist < current_radius:
            return float('inf'), True # Closed
        elif dist < current_radius + 500:
            capacity *= 0.2 # Severely slowed
```
Hazards form a strictly impenetrable hard-closure inner radius. Moving within 500m of that radius severely limits capacity.
- **Closure Semantics & Infeasibility:** Explicitly removes any edge where `edge_id in world_state.closed_edges`. Infeasible runs are dynamically pre-filtered using `is_infeasible()` which does a `nx.shortest_path()` on a clairvoyant graph with all closures/hazards at `depart_time`.
- **Scenario Types (`scenario.py`):**
  - **All:** 1 Incident injected (RNG Stream: `seed_val + 1`)
  - **Hazard/Mixed:** 1 expanding hazard injected (RNG Stream: `seed_val + 2`)
  - **Closure/Mixed:** 1 closed bi-directional edge (RNG Stream: `seed_val + 3`)
- **Observation Model:**
  - Implemented in `observe_world_state()` by randomly skipping closures/hazards via `dropout_p` and rewinding time by `delay_s`.
  - **SENTINEL planner:** Receives the delayed `obs_world` (`WorldState`). Does not read true state.
  - **SENTINEL replan():** Receives `obs_world`. Does not read true state.
  - **Baseline B:** Receives `obs_world` every 60s. Does not read true state for routing.
  - **Baseline C:** Reads true `WorldState` statically at dispatch to pick nearest units, then delegates to Baseline B logic.

---

## 4. PREDICTION

- **FEATURE_COLS:** `["length", "free_flow_time", "mean_current_speed", "min_current_speed", "max_occupancy", "num_signals", "share_arterial", "min_distance_incident", "time_of_day", "demand_level", "forecast_trend"]`
- **Label Definitions:** Defined in loader/training implicitly via filtering: `df_train_reg = df_train[df_train["actual_eta_min"] < 999.0].copy()` and classification target `route_fails`.
- **Training Data:** `start_seed=100`, `num_scenarios=50`. Random scenario-wise shuffling for splits. 
- **Models:** Saved to `models/sentinel_models.pkl`. Contains XGBoost quantiles (10/50/90), a risk classifier, a residual model, conformal offset `q_hat`, and feature bound ranges.
- **OOD Detector:** Implemented in `inference.py` using absolute min/max bounds. Properly redirects to a deterministic current-speed baseline. The Residual Model was trained but is NOT wired into inference.
- **Latest Metrics (Seeds 100-149 Test Split):**
```text
  Free-Flow ETA - MAE: 6.94, RMSE: 22.18
  Current-Speed ETA - MAE: 1.09, RMSE: 18.06
  XGBoost P50 - MAE: 2.21, RMSE: 18.47
  P10/P90 Coverage (After Calib): 87.7% (target ~80%), Mean Width: 18.44 min
  Acc: 0.90, Prec: 0.81, Rec: 0.44, F1: 0.57, PR-AUC: 0.63
```

---

## 5. SENTINEL CONTROLLER

**Mission Execution:**
```python
def execute_sentinel(G, depart_node, target_node, depart_time, true_world, seed, delay_s, dropout_p):
    ...
    while current_node != target_node:
        ...
        needs_replan = False
        if not current_route or current_time - last_replan_time >= 60.0:
            needs_replan = True
        else:
            for edge_id in current_route:
                u, v = edge_id.split("->")
                if f"{u}->{v}" in obs_world.closed_edges:
                    needs_replan = True; break
            if not needs_replan:
                fails, prob, _, _ = predict_risk(G, current_route, obs_world.timestamp_utc, obs_world)
                if prob > 0.8: needs_replan = True
        if needs_replan:
            routes_resp = generate_and_score_routes(...)
            ...
```
- **Replan Triggers:** Every 60s, observed route closure, or `predict_risk > 0.8`.
- **Routing Weights (`config/default.yaml`):** `w1_eta_p50: 0.4`, `w2_eta_p90_spread: 0.2`, `w3_risk: 0.2`, `w4_hazard: 0.2`.
- **(a) Is "stay on current route" scored with the same cost function?** No. It explicitly only runs `predict_risk > 0.8` to decide whether to nuke the route.
- **(b) Is there hysteresis, a margin, or a minimum dwell time?** No. 
- **(c) Is there a progress guard or a fallback to Baseline-B behavior?** Yes. It reactively calculates a standard `nx.shortest_path` fallback exactly like Baseline B if encountering an immediate dynamic failure mid-traversal. Also uses a 200 iter infinite loop guard.
- **(d) Is there a cap on replans per mission?** No.
- **(e) Can the controller reverse onto the edge it just traversed?** Yes. The graph and pathing allow U-turns.
- **(f) Does the replan trigger read hazard_overlap directly?** Yes, it manually checks `in obs_world.closed_edges` first, bypassing the model.

---

## 6. RESILIENCE, HOSPITAL, OPTIMIZER, WHAT-IF, REPLAN

- **Resilience:** Computes route redundancy and risk. Fully utilized in `make demo`.
- **Hospital:** Used in `optimize_mission` and `rank_destinations`. Suitability is rejected if required capabilities are missing or `capacity <= 0`.
- **Optimizer (CP-SAT):** `optimize_mission` explicitly minimizes the `max_time` across all unit assignments to a single incident. Fails over gracefully to `greedy_mission_optimizer`. Limit: `1.5s`. Used in demo and mission experiments.
- **What-If/Replan:** Used in `make demo` (Counterfactual generation). Evaluates Delta ETA and Delta Resilience on manual closures.

---

## 7. EXPERIMENTS

**Structure & Baselines:**
- **Baseline B:** Simple shortest-path routing, replans every 60s without ML inference, drops closed edges upon collision.
- **Baseline C:** Selects nearest available unit statically, then utilizes Baseline B loop logic to reach the scene.
- **Completion/Mission Time:** `True` if it reaches `target_node`, `False` if it hits `NetworkXNoPath` or the 200 iter cap. Time computed via summing sequential `tt_s`.
- **Seed Ranges:** Train 100-149, Val 150-159, Test 200-249. Results on disk are on TEST seeds. **No model tuning occurred on test seeds.**

**Latest Results Table Snippet (`closed_loop_results.csv`):**
```csv
scenario_type,delay,dropout,seed,infeasible,comp_B,eta_B,comp_S,eta_S
mixed,0,0.0,200,0,1.0,13.32,0.0,
mixed,0,0.0,201,0,1.0,303.21,0.0,
mixed,0,0.0,202,0,1.0,2.48,1.0,203.87
mixed,0,0.0,203,0,1.0,271.11,0.0,
```
**Latest Test Averages:**
```text
[MIXED] D=0s, Drp=0.0 | N=48 (Inf=2) | Comp B:89.6% S:39.6% | S:34.27±28.84m vs B:11.84±18.64m | S saved: -22.43±23.16m
[ACCIDENT] D=0s, Drp=0.0 | N=50 (Inf=0) | Comp B:100.0% S:100.0% | S:3.46±0.25m vs B:2.55±0.19m | S saved: -0.90±0.14m
[HAZARD] D=0s, Drp=0.0 | N=50 (Inf=0) | Comp B:88.0% S:50.0% | S:11.22±11.07m vs B:4.32±2.55m | S saved: -6.90±11.29m
[CLOSURE] D=0s, Drp=0.0 | N=50 (Inf=0) | Comp B:100.0% S:100.0% | S:3.84±0.53m vs B:2.56±0.31m | S saved: -1.28±0.36m
--- MISSION LEVEL EXPERIMENT ---
Completion Rate -> C (Nearest): 76.0%, SENTINEL: 18.0%
Mission Time (n=9) -> C: 9.50±9.96m | S: 62.11±73.00m
```
- **Mission Level Script:** Uses identical controller logic (`execute_sentinel` and `execute_baseline_B`) as the single-vehicle grid. 

---

## 8. TESTS

- **Files:** `test_api.py`, `test_components.py`, `test_leakage.py`, `test_world.py`
- **Output:**
```text
tests/test_api.py::test_api_predict_eta PASSED
tests/test_api.py::test_api_generate_routes PASSED
tests/test_components.py::test_routing_rules PASSED
tests/test_components.py::test_resilience PASSED
tests/test_components.py::test_hospital_ranking PASSED
tests/test_components.py::test_optimizer_constraints_and_fallback PASSED
tests/test_components.py::test_whatif_replan PASSED
tests/test_components.py::test_no_future_knowledge PASSED
tests/test_components.py::test_conformal_calibration PASSED
tests/test_leakage.py::test_no_leakage_features PASSED
tests/test_world.py::test_scenario_generation FAILED
tests/test_world.py::test_city_graph_generation PASSED
========================= 1 failed, 11 passed in 2.58s =========================
```
*(Failure is due to unseeded `time.time()` mismatch in `WorldState.timestamp_utc`)*

---

## 9. ONE CHEAP DIAGNOSTIC

| Seed | Type Received | Hash (0s) | Hash (120s) | Differs? | Time (0s) | Time (120s) |
|---|---|---|---|---|---|---|
| 150 | DiGraph | 8ced09b2 | 8ced09b2 | No | inf | inf |
| 151 | DiGraph | 8ced09b2 | 8ced09b2 | No | inf | inf |
| 152 | DiGraph | 8ced09b2 | 8ced09b2 | No | 103.94 | 103.94 |
| 153 | DiGraph | 8ced09b2 | 8ced09b2 | No | inf | inf |
| 154 | DiGraph | 8ced09b2 | 8ced09b2 | No | 3.29 | 3.29 |

*(Note: `inf` mission times are due to the `iters > 200` infinite loop block).*

---

## 10. `make demo`

```text
PYTHONPATH=. python -m sentinel_ai.demo.demo_flagship
--- SENTINEL: Geoagentic Framework to Support Emergency Movement ---
Loading models (if any)...

[Step 1] Initializing Synthetic City + Units + Hospitals + Incident...
Incident INC_DEMO_1 at N_7_3. Requires: ['ambulance', 'police']
Ambulance U1 at N_0_0.

[Step 2 & 4] Generating Routes (Diverse k-shortest) + Roles + Scoring...
  Route R_0 | Role: primary | Score: 26.91 | P50 ETA: 7.68 min | Risk: 99.6%
    Reason: Best score

[Step 3 & 5] Prediction & Resilience Computation...
  Resilience Score: 29.1/100
  - Route redundancy is 40% based on available diverse paths.
  - No viable backup route identified.
  - Primary route success probability is 0%.
  - Hazard exposure score is 15%.

[Step 6] Hospital Destination Ranking...
  Hospital H2 | Suitable | Rank: 1 | ETA: 7.638469219207764
  Hospital H1 | Suitable | Rank: 2 | ETA: 7.701284408569336
  Hospital H3 | Unsuitable | Rank: None | ETA: None
    Reasons: ["Required capability 'trauma' unavailable."]
  Hospital H4 | Unsuitable | Rank: None | ETA: None
    Reasons: ["Required capability 'trauma' unavailable."]

[Step 7] Full Mission Optimizer (CP-SAT)...
  Expected Mission Time: 15.32 min
  - Unit U1 -> N_7_3 (ETA: 15.32 min)
  - Unit U3 -> N_7_3 (ETA: 7.63 min)

[Step 8] What-If Scenario: Road Closure...
  Delta ETA: -2.64 min
  Route Changed: True
  Delta Resilience: +0.92

[Step 9 & 10] Operator Approval & Execution...
  (Auto-approved) Executing baseline vs SENTINEL plan in Simulator...

[Step 11 & 12] Baseline vs SENTINEL Result (Mock Demo Table)
  | Scenario     | Baseline Mission Time | SENTINEL Mission Time |
  |--------------|-----------------------|-----------------------|
  | Demo (seed 42) | 14.5 min              | 15.3 min               |
```
*It successfully evaluates a cohesive loop utilizing the entire facade.*

---

## 11. MERGE READINESS

- **Contracts:** `docs/CONTRACTS.md` is mostly completely accurate, mapping perfectly to the endpoints inside `api.py`.
- **Data Loaders:** `load_dataset("sumo")` is completely unknown/absent.
- **FastAPI Integration Blockers:**
  1. `api.py` caches a global variable `_G = build_city_graph(seed=42)`. This will fail immediately on live dynamic environments because it's permanently linked to a static 10x10 synthetic grid graph.
  2. The prediction modules inherently rely on loading model files via relative paths from `os.path.join(model_dir, "sentinel_models.pkl")` dynamically.

---

## 12. HONEST ASSESSMENT

| Component | Status | Evidence |
| :--- | :--- | :--- |
| **API/Facade** | Works | `make demo` completes full chain |
| **Routing & Candidates** | Partial | Needs loop guards against CP-SAT bouncing; high failure rates |
| **Prediction Inference** | Partial | Deadlocks during parallelization; Residual targets not wired |
| **CP-SAT Optimizer** | Works | `tests/test_components.py` and `demo_flagship.py` |
| **Simulator / World** | Works | Synthetic execution flows appropriately without bugs |

**The 10 Facts You Must Know:**
1. SENTINEL is currently being crushed by the baselines because it enters infinite "U-turn" loops trying to avoid >0.8 risk paths (which are common in Hazard/Mixed scenarios).
2. Parallelization using standard Python `concurrent.futures` instantly deadlocks on macOS due to OpenMP/XGBoost fork issues.
3. The API binds to a static cached `_G` graph generated synthetically, rendering it completely useless for real SUMO integration until parameterized.
4. The Residual Prediction target is trained and persisted but entirely ignored during live inference.
5. `test_scenario_generation` consistently fails due to hard-coded `time.time()` references.
6. The underlying graph assumes grid distances natively for hazard logic, meaning complex real-world GIS coordinates will break it.
7. Risk evaluations are completely bypassing hysteresis; bouncing back and forth across edges is a mathematically legal strategy to the agent right now.
8. The environment drops features natively from Pandas logic directly into XGBoost matrices, resulting in massive scaling bottlenecks.
9. Baseline B (which just ignores risk completely and plows through warnings until physical limits are hit) routinely succeeds with significantly less computational friction.
10. `get_edge_delay_s` never fundamentally grounds out to a real backend, meaning everything we've scored so far is effectively tuning synthetic edge cases.

**Top 5 Unknowns:**
1. SUMO mapping mechanisms.
2. The exact real-world scaling characteristics of CP-SAT optimizations on non-synthetic city scales.
3. Real-world telemetry update cadence versus the arbitrary 60s replan delay implemented.
4. Hardware constraints of the final deployment machine (multi-threading).
5. Frontend consumption constraints.

**Estimated Fix Efforts:**
1. Fix API static graph caching for dynamic SUMO state: **S**
2. Fix `pytest` timestamp assertion: **S**
3. Wire up Residual Model in inference: **S**
4. Apply routing penalties/hysteresis to prevent U-turn infinite loops: **M**
5. Resolve macOS Multiprocessing deadlock natively for fast sweeps: **L**
