import os
import pickle
import numpy as np
import networkx as nx
from typing import Dict, Any, List, Tuple

from sentinel_ai.contracts import WorldState
from sentinel_ai.prediction.features import compute_route_features
from sentinel_ai.prediction.baselines import baseline_current_speed_eta
from sentinel_ai.prediction.train import FEATURE_COLS

_MODELS = None

def load_models(model_dir: str = None):
    global _MODELS
    if model_dir is None:
        model_dir = os.environ.get("SENTINEL_MODEL_DIR")
    if not model_dir or not os.path.isabs(model_dir):
        _MODELS = "baseline_fallback"
        return
        
    model_path = os.path.join(model_dir, "sentinel_models.pkl")
    if os.path.exists(model_path):
        with open(model_path, "rb") as f:
            _MODELS = pickle.load(f)
    else:
        _MODELS = "baseline_fallback"

def predict_eta(
    G: nx.DiGraph,
    route: List[str],
    depart_time_utc: float,
    world_state: WorldState,
    model_dir: str = None
) -> Tuple[float, float, float, float, str, float]:
    if _MODELS is None:
        load_models(model_dir)
        
    features = compute_route_features(G, route, depart_time_utc, world_state)
    baseline_eta = baseline_current_speed_eta(G, route, features)
    
    if _MODELS == "baseline_fallback" or _MODELS is None:
        return baseline_eta, baseline_eta, baseline_eta, baseline_eta, "baseline_fallback: missing or invalid SENTINEL_MODEL_DIR", 0.0
        
    try:
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
            
        X = np.array([[features.get(col, 0.0) for col in FEATURE_COLS]])
        
        # Residual predictions
        r_p10 = float(_MODELS["eta_p10"].predict(X)[0])
        r_p50 = float(_MODELS["eta_p50"].predict(X)[0])
        r_p90 = float(_MODELS["eta_p90"].predict(X)[0])
        
        cs_eta = baseline_eta
        
        p10 = cs_eta * np.exp(r_p10)
        p50 = cs_eta * np.exp(r_p50)
        p90 = cs_eta * np.exp(r_p90)
        
        q_hat = _MODELS.get("q_hat", 0.0)
        p10 = max(0.0, p10 - q_hat)
        p90 = p90 + q_hat
        
        if not _MODELS.get("use_ml_p50", True):
            return p10, cs_eta, p90, baseline_eta, "baseline_fallback", 0.9
            
        return p10, p50, p90, baseline_eta, "ml", 0.9
    except Exception as e:
        return baseline_eta, baseline_eta, baseline_eta, baseline_eta, "baseline_fallback", 0.0

def predict_risk(
    G: nx.DiGraph,
    route: List[str],
    depart_time_utc: float,
    world_state: WorldState,
    model_dir: str = None
) -> Tuple[bool, float, str, float]:
    if _MODELS is None:
        load_models(model_dir)
        
    features = compute_route_features(G, route, depart_time_utc, world_state)
    
    if _MODELS == "baseline_fallback" or _MODELS is None:
        fails = features.get("closed_edge_flag", 0.0) > 0.5
        prob = 1.0 if fails else 0.0
        return fails, prob, "baseline_fallback: missing or invalid SENTINEL_MODEL_DIR", 0.0
        
    try:
        ranges = _MODELS.get("feature_ranges", {})
        is_ood = False
        for col, (c_min, c_max) in ranges.items():
            if col in features:
                val = float(features[col])
                if val < c_min or val > c_max:
                    is_ood = True
                    break
        if is_ood:
            fails = features.get("min_distance_incident", 10000.0) < 500.0
            prob = 1.0 if fails else 0.0
            return fails, prob, "baseline_fallback", 0.0
            
        X = np.array([[features.get(col, 0.0) for col in FEATURE_COLS]])
        prob = float(_MODELS["risk_clf"].predict_proba(X)[0, 1])
        fails = prob > 0.5
        return fails, prob, "ml", 0.85
    except Exception:
        fails = features.get("closed_edge_flag", 0.0) > 0.5
        prob = 1.0 if fails else 0.0
        return fails, prob, "baseline_fallback", 0.0
