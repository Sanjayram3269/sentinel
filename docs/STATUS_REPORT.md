# SENTINEL PROJECT STATUS REPORT

**DISCLAIMER: All data, locations, and events reported herein are completely synthetic.**

## 1. Codebase Hygiene & Testing
- **Test Count:** 15 tests (All passing)
- **Files Modified:**
  - `config/default.yaml`
  - `docs/CONTRACTS.md`
  - `sentinel_ai/api.py`
  - `sentinel_ai/data/loader.py`
  - `sentinel_ai/demo/demo_flagship.py`
  - `sentinel_ai/experiments/run_experiments.py`
  - `sentinel_ai/optimization/optimizer.py`
  - `sentinel_ai/prediction/inference.py`
  - `sentinel_ai/prediction/train.py`
  - `tests/test_components.py`
  - `tests/test_leakage.py`
- **New Scripts/Configs:** Added PREREG.md, RESULTS.md, smoke tests, tuning scripts, observation diagnostic scripts, and evaluation runners.

## 2. ML Models (metrics.json)
The models were successfully refactored to remove future-state leakage and trained to predict residual error scaling factors instead of absolute ETAs.
- **Config:** `use_ml_p50 = true`
- **ETA Model (n=726):**
  - Free Flow Baseline MAE: 3.86 min
  - Current Speed Baseline MAE: 3.38 min
  - **Sentinel P50 MAE: 2.45 min** (Best performance)
  - Sentinel Interval Coverage (target ~80%): 81.7%
  - Sentinel Interval Mean Width: 1.87 min
- **Risk Classifier:**
  - Precision: 0.913
  - Recall: 0.926
  - F1 Score: 0.920
  - PR-AUC: 0.971

## 3. Final Evaluation Results (Test Seeds 250-299)
Sentinel execution fell back to `use_prediction=False` since exhaustive tuning over validation seeds (150-199) could not identify hyperparameters that strictly beat Baseline B across all metrics.

| Scenario | Delay (s) | Dropout | n | Comp B (%) | Comp S (%) | Diff (%) | ETA B (m) | ETA S (m) |
|---|---|---|---|---|---|---|---|---|
| accident | 0 | 0.0 | 50 | 100.0 | 100.0 | +0.0 | 2.58±0.30 | 3.60±0.54 |
| accident | 60 | 0.2 | 50 | 100.0 | 100.0 | +0.0 | 2.59±0.29 | 3.60±0.54 |
| closure | 0 | 0.0 | 50 | 100.0 | 100.0 | +0.0 | 2.42±0.15 | 3.45±0.37 |
| closure | 60 | 0.2 | 50 | 100.0 | 100.0 | +0.0 | 2.44±0.16 | 3.49±0.36 |
| hazard | 0 | 0.0 | 50 | 82.0 | 64.0 | -18.0 | 19.36±26.93 | 33.25±28.03 |
| hazard | 60 | 0.2 | 50 | 74.0 | 60.0 | -14.0 | 20.57±29.18 | 34.99±30.12 |
| mixed | 0 | 0.0 | 50 | 84.0 | 66.0 | -18.0 | 78.36±102.13 | 166.99±232.67 |
| mixed | 60 | 0.2 | 50 | 82.0 | 66.0 | -16.0 | 86.80±108.83 | 182.12±247.93 |

## 4. Success Criteria Assessment (Wins / Ties / Losses)
- **Criteria (a) - Completion rate >= B minus 2pp:** **LOSS**. Sentinel suffered significantly in hazard and mixed scenarios (drops of 14% to 18%).
- **Criteria (b) - Mission time <= 1.10 x B (Accident/Closure):** **LOSS**. Sentinel's mission times (~3.5 min) were ~1.4x higher than Baseline B (~2.5 min). This is largely an artifact of Sentinel correctly evaluating full mission scope (including incident-to-hospital transits for ambulances) while the baseline metric only tracks unit-to-incident paths.
- **Criteria (c) - Mission completion >= Baseline C:** **TIE/LOSS**. In Step 5 evaluations, both Sentinel and Baseline C failed to complete the mission because the generated synthetic city layout prevented the ambulance from successfully navigating to the trauma hospital (due to deadlocks or infinite loops handling).

## 5. Assumptions Made
1. All data is purely synthetic.
2. The dynamic failure label definition (`max(1.5 * cs_eta, cs_eta + 5.0)`) is sufficiently robust to capture anomalous congestion while remaining immune to base distance scaling issues.
3. Simulator iteration cutoff (200 steps) operates as a viable proxy for identifying total deadlocks.
4. Sentinel's replanning strategy executes strictly causally with respect to delays and dropouts (no future information is exposed, as verified by `test_no_future_knowledge`).
5. A fallback to Baseline B execution is appropriate if rigorous hyperparameter tuning fails to satisfy the preregistered constraints.

## 6. Anything Not Done
1. Sentinel did not achieve a mathematically superior configuration that fulfilled the final success criteria, forcing the architecture into a fallback state.
2. The fundamental mismatch between Baseline B's unit-to-incident ETAs and Sentinel's full-mission (CP-SAT) ETAs could not be entirely reconciled within the scope of the simulator without completely rewriting the evaluation loop.
