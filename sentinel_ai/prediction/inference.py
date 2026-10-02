import os
import pickle
import pandas as pd
import networkx as nx
from typing import Dict, Any, List, Tuple

from sentinel_ai.contracts import WorldState
from sentinel_ai.prediction.features import compute_route_features
from sentinel_ai.prediction.baselines import baseline_current_speed_eta
from sentinel_ai.prediction.train import FEATURE_COLS

_MODELS = None

def load_models(model_dir: str = "models"):
    global _MODELS
    model_path = os.path.join(model_dir, "sentinel_models.pkl")
    if os.path.exists(model_path):
        with open(model_path, "rb") as f:
            _MODELS = pickle.load(f)
    else:
        _MODELS = None

def predict_eta(
    G: nx.DiGraph,
    route: List[str],
    depart_time_utc: float,
    world_state: WorldState,
    model_dir: str = "models"
) -> Tuple[float, float, float, float, str, float]:
    """Returns (p10, p50, p90, baseline, source, confidence)"""
    if _MODELS is None:
        load_models(model_dir)
        
    features = compute_route_features(G, route, depart_time_utc, world_state)
    baseline_eta = baseline_current_speed_eta(G, route, features)
    
    if _MODELS is None:
        return baseline_eta, baseline_eta, baseline_eta, baseline_eta, "baseline_fallback", 0.0
        
    try:
        # OOD Check
        ranges = _MODELS.get("feature_ranges", {})
        is_ood = False
        for col, (c_min, c_max) in ranges.items():
            if col in features:
                val = float(features[col])
                if val < c_min or val > c_max:
                    is_ood = True
                    break
                    
        if is_ood:
            return baseline_eta, baseline_eta, baseline_eta, baseline_eta, "baseline_fallback", 0.0
            
        import numpy as np
        X = np.array([[features.get(col, 0.0) for col in FEATURE_COLS]])
        p10 = float(_MODELS["eta_p10"].predict(X)[0])
        p50 = float(_MODELS["eta_p50"].predict(X)[0])
        p90 = float(_MODELS["eta_p90"].predict(X)[0])
        
        q_hat = _MODELS.get("q_hat", 0.0)
        p10 = max(0.0, p10 - q_hat)
        p90 = p90 + q_hat
        
        return p10, p50, p90, baseline_eta, "ml", 0.9
    except Exception:
        return baseline_eta, baseline_eta, baseline_eta, baseline_eta, "baseline_fallback", 0.0

def predict_risk(
    G: nx.DiGraph,
    route: List[str],
    depart_time_utc: float,
    world_state: WorldState,
    model_dir: str = "models"
) -> Tuple[bool, float, str, float]:
    """Returns (route_fails, probability, source, confidence)"""
    if _MODELS is None:
        load_models(model_dir)
        
    features = compute_route_features(G, route, depart_time_utc, world_state)
    
    if _MODELS is None:
        # Fallback: if there's a closed edge, it fails, else false
        fails = features.get("closed_edge_flag", 0.0) > 0.5
        prob = 1.0 if fails else 0.0
        return fails, prob, "baseline_fallback", 0.0
        
    try:
        # OOD Check
        ranges = _MODELS.get("feature_ranges", {})
        is_ood = False
        for col, (c_min, c_max) in ranges.items():
            if col in features:
                val = float(features[col])
                if val < c_min or val > c_max:
                    is_ood = True
                    break
        if is_ood:
            # Fallback
            fails = features.get("min_distance_incident", 10000.0) < 500.0 # heuristic fallback
            prob = 1.0 if fails else 0.0
            return fails, prob, "baseline_fallback", 0.0
            
        import numpy as np
        X = np.array([[features.get(col, 0.0) for col in FEATURE_COLS]])
        prob = float(_MODELS["risk_clf"].predict_proba(X)[0, 1])
        fails = prob > 0.5
        return fails, prob, "ml", 0.85
    except Exception:
        fails = features.get("closed_edge_flag", 0.0) > 0.5
        prob = 1.0 if fails else 0.0
        return fails, prob, "baseline_fallback", 0.0
