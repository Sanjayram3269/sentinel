# SENTINEL AI / Optimization Module

This is the AI/Optimization backend for SENTINEL (Geoagentic Framework to Support Emergency Movement).

## Setup
1. `python3 -m venv .venv`
2. `source .venv/bin/activate`
3. `pip install numpy pandas scikit-learn xgboost ortools networkx pydantic pyyaml pytest matplotlib`

## Commands
- `make test`: Run all unit tests (uses fixed seeds).
- `make train`: Train the XGBoost models on simulated data and save to `models/`.
- `make demo`: Run the deterministic flagship loop step by step.
- `make experiments`: Run experiments comparing baselines to SENTINEL and generate plots/CSV.

## Results
Please note that all outputs and metrics are derived from a **synthetic simulator** designed to stand in for SUMO. 

## Merging with the backend and SUMO
To integrate this module into the larger system:
1. Update `sentinel_ai/data/loader.py`: Currently, `load_dataset("sumo")` raises a `NotImplementedError`. It must be implemented to load real SUMO features matching the exact column spec.
2. Use the facade `sentinel_ai/api.py`. It is the **only** entry point the backend should call.
3. Every response includes `contract_version`, `model_version`, `source` (ml or baseline_fallback), `confidence`, `reasons`, and `evidence`.
4. Ensure units match: ETA is in minutes, distances in meters, speed in km/h, timestamps are UTC.

# sentinel_abhy_version

# sentinel
