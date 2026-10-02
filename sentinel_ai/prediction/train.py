import os
import pickle
import xgboost as xgb
import pandas as pd
from typing import Dict, Any
from sklearn.metrics import mean_absolute_error, mean_squared_error, accuracy_score, precision_score, recall_score

from sentinel_ai.data.loader import load_dataset

FEATURE_COLS = [
    "length", "free_flow_time", "mean_current_speed", "min_current_speed",
    "max_occupancy", "num_signals", "share_arterial", "min_distance_incident",
    "time_of_day", "demand_level", "forecast_trend"
]

def train_models(model_dir: str = "models", num_scenarios: int = 50):
    print("Generating dataset...")
    df = load_dataset(source="synthetic", num_scenarios=num_scenarios, start_seed=100)
    
    # Split by scenario_id
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
    X_cal_reg = df_cal_reg[FEATURE_COLS]
    y_cal_reg = df_cal_reg["actual_eta_min"]
    X_test_reg = df_test_reg[FEATURE_COLS]
    y_test_reg = df_test_reg["actual_eta_min"]
    
    X_train_clf = df_train[FEATURE_COLS]
    y_train_clf = df_train["route_fails"]
    X_test_clf = df_test[FEATURE_COLS]
    y_test_clf = df_test["route_fails"]
    
    print("Training ETA Quantile Regressors...")
    models = {}
    
    for q in [0.1, 0.5, 0.9]:
        reg = xgb.XGBRegressor(
            objective="reg:quantileerror",
            quantile_alpha=q,
            n_estimators=100,
            learning_rate=0.1,
            max_depth=5,
            random_state=42
        )
        reg.fit(X_train_reg, y_train_reg)
        models[f"eta_p{int(q*100)}"] = reg
        
    print("Training Risk Classifier...")
    clf = xgb.XGBClassifier(
        n_estimators=100,
        learning_rate=0.1,
        max_depth=5,
        random_state=42,
        eval_metric="logloss"
    )
    clf.fit(X_train_clf, y_train_clf)
    models["risk_clf"] = clf
    
    print("Calibrating Conformal Intervals...")
    from sentinel_ai.prediction.calibration import calibrate_conformal
    q_hat = calibrate_conformal(models, X_cal_reg, y_cal_reg, alpha=0.2)
    models["q_hat"] = q_hat
    print(f"  Calibration q_hat: {q_hat:.3f}")
    
    print("Training Residual Model (actual / current_speed)...")
    def get_cs_eta(df_features, y_actual):
        cs_etas = []
        for _, row in df_features.iterrows():
            speed_ms = row["mean_current_speed"] * 1000 / 3600 if row["mean_current_speed"] > 0 else 0.001
            cs_eta = (row["length"] / speed_ms) / 60.0
            cs_etas.append(cs_eta)
        return np.array(cs_etas)
        
    cs_train = get_cs_eta(X_train_reg, y_train_reg)
    residual_target = y_train_reg / cs_train
    residual_reg = xgb.XGBRegressor(n_estimators=100, learning_rate=0.1, max_depth=5, random_state=42)
    residual_reg.fit(X_train_reg, residual_target)
    models["residual_model"] = residual_reg
    
    # Save feature OOD bounds
    ranges = {}
    for col in FEATURE_COLS:
        ranges[col] = (float(X_train_reg[col].min()), float(X_train_reg[col].max()))
    models["feature_ranges"] = ranges
    
    os.makedirs(model_dir, exist_ok=True)
    model_path = os.path.join(model_dir, "sentinel_models.pkl")
    with open(model_path, "wb") as f:
        import pickle
        pickle.dump(models, f)
        
    print("--- BASELINE & REGRESSION EVALUATION (Held-out Scenarios) ---")
    from sklearn.metrics import mean_squared_error, mean_absolute_error
    import numpy as np
    
    p50_preds = models["eta_p50"].predict(X_test_reg)
    p10_preds = models["eta_p10"].predict(X_test_reg)
    p90_preds = models["eta_p90"].predict(X_test_reg)
    
    p10_calib = np.maximum(0.0, p10_preds - q_hat)
    p90_calib = p90_preds + q_hat
    
    # Calculate current_speed_eta for test set
    def current_speed_eta(row):
        mean_speed = row["mean_current_speed"]
        if mean_speed <= 0:
            return row["baseline_ff_eta"]
        speed_ms = mean_speed * 1000 / 3600
        return (row["length"] / speed_ms) / 60.0
        
    df_test_reg["current_speed_eta"] = df_test_reg.apply(current_speed_eta, axis=1)
    
    ff_eta_test = df_test_reg["baseline_ff_eta"]
    curr_eta_test = df_test_reg["current_speed_eta"]
    
    print(f"  Free-Flow ETA - MAE: {mean_absolute_error(y_test_reg, ff_eta_test):.2f}, RMSE: {np.sqrt(mean_squared_error(y_test_reg, ff_eta_test)):.2f}")
    print(f"  Current-Speed ETA - MAE: {mean_absolute_error(y_test_reg, curr_eta_test):.2f}, RMSE: {np.sqrt(mean_squared_error(y_test_reg, curr_eta_test)):.2f}")
    print(f"  XGBoost P50 - MAE: {mean_absolute_error(y_test_reg, p50_preds):.2f}, RMSE: {np.sqrt(mean_squared_error(y_test_reg, p50_preds)):.2f}")
    
    covered_before = ((y_test_reg >= p10_preds) & (y_test_reg <= p90_preds)).mean()
    width_before = (p90_preds - p10_preds).mean()
    print(f"  P10/P90 Coverage (Before Calib): {covered_before*100:.1f}%, Mean Width: {width_before:.2f} min")
    
    covered_after = ((y_test_reg >= p10_calib) & (y_test_reg <= p90_calib)).mean()
    width_after = (p90_calib - p10_calib).mean()
    print(f"  P10/P90 Coverage (After Calib): {covered_after*100:.1f}% (target ~80%), Mean Width: {width_after:.2f} min")
    
    print("\n  ETA Error by Scenario Type:")
    df_test_reg["p50_preds"] = p50_preds
    for scenario_type in ["mixed", "accident", "hazard", "closure"]:
        st_rows = df_test_reg[df_test_reg["scenario_type"] == scenario_type]
        if not st_rows.empty:
            mae = mean_absolute_error(st_rows["actual_eta_min"], st_rows["p50_preds"])
            rmse = np.sqrt(mean_squared_error(st_rows["actual_eta_min"], st_rows["p50_preds"]))
            mae_ff = mean_absolute_error(st_rows["actual_eta_min"], st_rows["baseline_ff_eta"])
            rmse_ff = np.sqrt(mean_squared_error(st_rows["actual_eta_min"], st_rows["baseline_ff_eta"]))
            mae_cs = mean_absolute_error(st_rows["actual_eta_min"], st_rows["current_speed_eta"])
            rmse_cs = np.sqrt(mean_squared_error(st_rows["actual_eta_min"], st_rows["current_speed_eta"]))
            print(f"    {scenario_type.upper()} (n={len(st_rows)}):")
            print(f"      FF: MAE {mae_ff:.2f}, RMSE {rmse_ff:.2f}")
            print(f"      CS: MAE {mae_cs:.2f}, RMSE {rmse_cs:.2f}")
            print(f"      P50: MAE {mae:.2f}, RMSE {rmse:.2f}")
    
    print("\n--- CLASSIFIER EVALUATION (Held-out Scenarios) ---")
    from sklearn.metrics import f1_score, average_precision_score, confusion_matrix
    preds_clf = clf.predict(X_test_clf)
    probs_clf = clf.predict_proba(X_test_clf)[:, 1]
    
    acc = accuracy_score(y_test_clf, preds_clf)
    prec = precision_score(y_test_clf, preds_clf, zero_division=0)
    rec = recall_score(y_test_clf, preds_clf, zero_division=0)
    f1 = f1_score(y_test_clf, preds_clf, zero_division=0)
    pr_auc = average_precision_score(y_test_clf, probs_clf)
    cm = confusion_matrix(y_test_clf, preds_clf)
    
    print(f"  Acc: {acc:.2f}, Prec: {prec:.2f}, Rec: {rec:.2f}, F1: {f1:.2f}, PR-AUC: {pr_auc:.2f}")
    print(f"  Confusion Matrix:\n{cm}")
    
    print(f"\nModels saved to {model_path}")
    
if __name__ == "__main__":
    train_models()
