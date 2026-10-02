# PRE-REGISTRATION

## Hypotheses / Success Criteria
(a) Completion rate: SENTINEL must achieve a completion rate >= Baseline B minus 2 percentage points in every scenario type.
(b) Mission time: In Accident and Closure scenarios, SENTINEL's mission time must be <= 1.10 x Baseline B's mission time.
(c) Mission completion: SENTINEL's full-mission completion rate must be >= Baseline C (the greedy baseline).

## Data & Method
- Test Seeds: 250-299.
- Scenarios: accident, closure, hazard, mixed.
- Observation settings: (delay=0s, dropout=0.0) and (delay=60s, dropout=0.2).
- Execution: Serial, single-threaded XGBoost.
