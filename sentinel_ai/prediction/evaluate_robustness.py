import numpy as np
import pandas as pd
from sklearn.metrics import mean_absolute_error, mean_squared_error, accuracy_score, precision_score, recall_score, f1_score
from sentinel_ai.world.scenario import generate_scenario
from sentinel_ai.world.simulator import execute_route
from sentinel_ai.prediction.inference import load_models
from sentinel_ai.prediction.train import FEATURE_COLS
from sentinel_ai.data.loader import k_shortest_paths
from sentinel_ai.prediction.features import compute_route_features
import sentinel_ai.world.simulator as sim
from sentinel_ai.world.city_graph import build_city_graph
import random

def run_robustness_check():
    # Shift parameters
    sim.BPR_ALPHA = 0.3 # was 0.15
    sim.BPR_BETA = 5.0 # was 4.0
    
    G = build_city_graph(seed=999) # different city seed
    load_models()
    from sentinel_ai.prediction.inference import _MODELS
    
    if _MODELS is None:
        print("Models not found.")
        return
        
    print("Evaluating on shifted simulator (BPR_ALPHA=0.3, BPR_BETA=5.0, City Seed=999)...")
    
    nodes = list(G.nodes())
    data_rows = []
    
    for i in range(10): # 10 scenarios
        seed = 300 + i
        scenario_type = random.choice(["mixed", "accident", "hazard", "closure"])
        world_state = generate_scenario(f"ROBUST_{i}", seed, scenario_type)
        world_state.demand_level *= 1.5 # unseen higher demand
        
        random.seed(seed)
        for _ in range(5):
            u = random.choice(nodes)
            v = random.choice(nodes)
            if u == v: continue
            
            paths = k_shortest_paths(G, u, v, k=3)
            for path in paths:
                route_edges = [f"{path[j]}->{path[j+1]}" for j in range(len(path)-1)]
                features = compute_route_features(G, route_edges, world_state.timestamp_utc, world_state)
                actual_eta = execute_route(G, route_edges, world_state.timestamp_utc, world_state, seed=seed)
                
                route_fails = actual_eta == float('inf') or actual_eta > 1.5 * features["free_flow_time"]
                
                row = {
                    "actual_eta_min": min(actual_eta, 999.0) if actual_eta != float('inf') else 999.0,
                    "route_fails": int(route_fails)
                }
                row.update(features)
                data_rows.append(row)
                
    df = pd.DataFrame(data_rows)
    df_reg = df[df["actual_eta_min"] < 999.0].copy()
    
    X_reg = df_reg[FEATURE_COLS]
    y_reg = df_reg["actual_eta_min"]
    
    X_clf = df[FEATURE_COLS]
    y_clf = df["route_fails"]
    
    p50_preds = _MODELS["eta_p50"].predict(X_reg)
    mae = mean_absolute_error(y_reg, p50_preds)
    rmse = np.sqrt(mean_squared_error(y_reg, p50_preds))
    
    # Calculate current_speed_eta for test set
    def current_speed_eta(row):
        mean_speed = row["mean_current_speed"]
        if mean_speed <= 0:
            return row["free_flow_time"]
        speed_ms = mean_speed * 1000 / 3600
        return (row["length"] / speed_ms) / 60.0
        
    df_reg["current_speed_eta"] = df_reg.apply(current_speed_eta, axis=1)
    ff_eta_test = df_reg["free_flow_time"]
    curr_eta_test = df_reg["current_speed_eta"]
    
    ff_mae = mean_absolute_error(y_reg, ff_eta_test)
    ff_rmse = np.sqrt(mean_squared_error(y_reg, ff_eta_test))
    cs_mae = mean_absolute_error(y_reg, curr_eta_test)
    cs_rmse = np.sqrt(mean_squared_error(y_reg, curr_eta_test))
    
    print(f"Robustness FF - MAE: {ff_mae:.2f}, RMSE: {ff_rmse:.2f}")
    print(f"Robustness CS - MAE: {cs_mae:.2f}, RMSE: {cs_rmse:.2f}")
    print(f"Robustness XGBoost P50 - MAE: {mae:.2f}, RMSE: {rmse:.2f}")
    
    if "residual_model" in _MODELS:
        residual_preds = _MODELS["residual_model"].predict(X_reg)
        hybrid_preds = curr_eta_test * residual_preds
        hybrid_mae = mean_absolute_error(y_reg, hybrid_preds)
        hybrid_rmse = np.sqrt(mean_squared_error(y_reg, hybrid_preds))
        print(f"Robustness Hybrid - MAE: {hybrid_mae:.2f}, RMSE: {hybrid_rmse:.2f}")
    
    preds_clf = _MODELS["risk_clf"].predict(X_clf)
    acc = accuracy_score(y_clf, preds_clf)
    prec = precision_score(y_clf, preds_clf, zero_division=0)
    rec = recall_score(y_clf, preds_clf, zero_division=0)
    f1 = f1_score(y_clf, preds_clf, zero_division=0)
    pos_rate = np.mean(preds_clf)
    true_pos_rate = np.mean(y_clf)
    print(f"Robustness Classifier - Acc: {acc:.2f}, Prec: {prec:.2f}, Rec: {rec:.2f}, F1: {f1:.2f}")
    print(f"Robustness Classifier Positive Rate: {pos_rate*100:.1f}% (True: {true_pos_rate*100:.1f}%)")
    
    # Restore defaults for other tests
    sim.BPR_ALPHA = 0.15
    sim.BPR_BETA = 4.0

if __name__ == "__main__":
    run_robustness_check()
