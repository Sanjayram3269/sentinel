import numpy as np
import pandas as pd
from typing import Dict, Any, Tuple

def calibrate_conformal(models: Dict[str, Any], X_cal: pd.DataFrame, y_cal: pd.Series, alpha: float = 0.2) -> float:
    """
    Computes a conformal prediction adjustment factor using a calibration set.
    For alpha=0.2, target coverage is 80%.
    """
    p10_preds = models["eta_p10"].predict(X_cal)
    p90_preds = models["eta_p90"].predict(X_cal)
    
    # Calculate non-conformity scores
    # Score = max(lower - y, y - upper)
    scores = np.maximum(p10_preds - y_cal, y_cal - p90_preds)
    
    # Find the (1 - alpha) quantile of scores
    n = len(scores)
    q_level = np.ceil((n + 1) * (1 - alpha)) / n
    q_level = min(q_level, 1.0)
    
    q_hat = np.quantile(scores, q_level, method="higher")
    return float(max(0.0, q_hat))

def apply_calibration(p10: float, p90: float, q_hat: float) -> Tuple[float, float]:
    return max(0.0, p10 - q_hat), p90 + q_hat
