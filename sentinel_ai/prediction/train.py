import os
import json
import hashlib
import pickle
import numpy as np
import pandas as pd
from typing import Dict, Any
from sklearn.metrics import mean_absolute_error, mean_squared_error, accuracy_score, precision_score, recall_score, f1_score, average_precision_score, confusion_matrix

from sentinel_ai.data.loader import load_dataset
from sentinel_ai.prediction.calibration import calibrate_conformal

FEATURE_COLS = [
    "length", "free_flow_time", "mean_current_speed", "min_current_speed",
    "max_occupancy", "num_signals", "share_arterial", "min_distance_incident",
    "time_of_day", "demand_level", "forecast_trend"
]

def get_cs_eta(df_features):
    cs_etas = []
    for _, row in df_features.iterrows():
        speed_ms = row["mean_current_speed"] * 1000 / 3600 if row["mean_current_speed"] > 0 else 0.001
        cs_eta = (row["length"] / speed_ms) / 60.0
        cs_etas.append(cs_eta)
    return np.array(cs_etas)

def train_models(model_dir: str = "models", num_scenarios: int = 150):
    import xgboost as xgb
    print("Generating dataset...")
    df = load_dataset(source="synthetic", num_scenarios=num_scenarios, start_seed=300)
    
    scenario_ids = df["scenario_id"].unique()
    import random
    random.seed(42)
    random.shuffle(scenario_ids)
    
    split_1 = int(len(scenario_ids) * 0.6)
    split_2 = int(len(scenario_ids) * 0.8)
    train_scenarios = set(scenario_ids[:split_1])
    cal_scenarios = set(scenario_ids[split_1:split_2])
    test_scenarios = set(scenario_ids[split_2:])
    
    df_train = df[df["scenario_id"].isin(train_scenarios)].copy()
    df_cal = df[df["scenario_id"].isin(cal_scenarios)].copy()
    df_test = df[df["scenario_id"].isin(test_scenarios)].copy()
    
    df_train_reg = df_train[df_train["actual_eta_min"] < 999.0].copy()
    df_cal_reg = df_cal[df_cal["actual_eta_min"] < 999.0].copy()
    df_test_reg = df_test[df_test["actual_eta_min"] < 999.0].copy()
    
    X_train_reg = df_train_reg[FEATURE_COLS]
    y_train_reg = df_train_reg["actual_eta_min"]
    cs_train = get_cs_eta(X_train_reg)
    y_train_res = np.log(y_train_reg / cs_train)
    
    X_cal_reg = df_cal_reg[FEATURE_COLS]
    y_cal_reg = df_cal_reg["actual_eta_min"]
    cs_cal = get_cs_eta(X_cal_reg)
    y_cal_res = np.log(y_cal_reg / cs_cal)
    
    X_test_reg = df_test_reg[FEATURE_COLS]
    y_test_reg = df_test_reg["actual_eta_min"]
    cs_test = get_cs_eta(X_test_reg)
    
    X_train_clf = df_train[FEATURE_COLS]
    y_train_clf = df_train["route_fails"]
    X_test_clf = df_test[FEATURE_COLS]
    y_test_clf = df_test["route_fails"]
    
    print("Training Residual Regressors...")
    models = {}
    for q in [0.1, 0.5, 0.9]:
        reg = xgb.XGBRegressor(objective="reg:quantileerror", quantile_alpha=q, n_estimators=100, learning_rate=0.1, max_depth=5, random_state=42)
        reg.fit(X_train_reg, y_train_res)
        models[f"eta_p{int(q*100)}"] = reg
        
    print("Training Risk Classifier...")
    clf = xgb.XGBClassifier(n_estimators=100, learning_rate=0.1, max_depth=5, random_state=42, eval_metric="logloss")
    clf.fit(X_train_clf, y_train_clf)
    models["risk_clf"] = clf
    
    print("Calibrating Conformal Intervals...")
    # Predict p10/p90 on calibration set
    res_p10_cal = models["eta_p10"].predict(X_cal_reg)
    res_p90_cal = models["eta_p90"].predict(X_cal_reg)
    p10_cal = cs_cal * np.exp(res_p10_cal)
    p90_cal = cs_cal * np.exp(res_p90_cal)
    
    # We conformalize directly on the ETA scale
    # q_hat is computed for the upper and lower bounds
    errors = np.maximum(p10_cal - y_cal_reg, y_cal_reg - p90_cal)
    errors = np.maximum(0, errors) # Only care if outside
    alpha = 0.2
    n = len(y_cal_reg)
    q_idx = int(np.ceil((n + 1) * (1 - alpha)))
    if q_idx >= n: q_idx = n - 1
    q_hat = np.sort(errors)[q_idx]
    models["q_hat"] = float(q_hat)
    
    ranges = {}
    for col in FEATURE_COLS:
        ranges[col] = (float(X_train_reg[col].min()), float(X_train_reg[col].max()))
    models["feature_ranges"] = ranges
    
    # Evaluate
    res_p50_test = models["eta_p50"].predict(X_test_reg)
    res_p10_test = models["eta_p10"].predict(X_test_reg)
    res_p90_test = models["eta_p90"].predict(X_test_reg)
    
    p50_preds = cs_test * np.exp(res_p50_test)
    p10_preds = np.maximum(0.0, cs_test * np.exp(res_p10_test) - q_hat)
    p90_preds = cs_test * np.exp(res_p90_test) + q_hat
    
    ff_eta_test = df_test_reg["baseline_ff_eta"].values
    
    mae_ff = float(mean_absolute_error(y_test_reg, ff_eta_test))
    rmse_ff = float(np.sqrt(mean_squared_error(y_test_reg, ff_eta_test)))
    
    mae_cs = float(mean_absolute_error(y_test_reg, cs_test))
    rmse_cs = float(np.sqrt(mean_squared_error(y_test_reg, cs_test)))
    
    mae_ml = float(mean_absolute_error(y_test_reg, p50_preds))
    rmse_ml = float(np.sqrt(mean_squared_error(y_test_reg, p50_preds)))
    
    # Decision Rule
    use_ml_p50 = mae_ml <= 0.9 * mae_cs
    models["use_ml_p50"] = use_ml_p50
    print(f"Decision Rule: MAE_ML={mae_ml:.2f}, 0.9*MAE_CS={0.9*mae_cs:.2f} -> use_ml_p50={use_ml_p50}")
    
    covered = float(((y_test_reg >= p10_preds) & (y_test_reg <= p90_preds)).mean())
    width = float((p90_preds - p10_preds).mean())
    
    preds_clf = clf.predict(X_test_clf)
    probs_clf = clf.predict_proba(X_test_clf)[:, 1]
    acc = float(accuracy_score(y_test_clf, preds_clf))
    prec = float(precision_score(y_test_clf, preds_clf, zero_division=0))
    rec = float(recall_score(y_test_clf, preds_clf, zero_division=0))
    f1 = float(f1_score(y_test_clf, preds_clf, zero_division=0))
    pr_auc = float(average_precision_score(y_test_clf, probs_clf))
    pos_rate = float(y_test_clf.mean())
    
    os.makedirs(model_dir, exist_ok=True)
    model_path = os.path.join(model_dir, "sentinel_models.pkl")
    with open(model_path, "wb") as f:
        pickle.dump(models, f)
        
    with open(model_path, "rb") as f:
        file_hash = hashlib.md5(f.read()).hexdigest()
        
    metrics = {
        "seeds_used": "300-449",
        "artifact_hash": file_hash,
        "use_ml_p50": use_ml_p50,
        "eta_evaluation": {
            "n": len(y_test_reg),
            "free_flow": {"mae": mae_ff, "rmse": rmse_ff},
            "current_speed": {"mae": mae_cs, "rmse": rmse_cs},
            "sentinel_p50": {"mae": mae_ml, "rmse": rmse_ml},
            "interval_coverage": covered,
            "interval_mean_width": width
        },
        "classifier_evaluation": {
            "precision": prec,
            "recall": rec,
            "f1": f1,
            "pr_auc": pr_auc,
            "positive_rate": pos_rate
        }
    }
    
    with open(os.path.join(model_dir, "metrics.json"), "w") as f:
        json.dump(metrics, f, indent=2)
        
    print(json.dumps(metrics, indent=2))

if __name__ == "__main__":
    train_models()
