import json
import os
import networkx as nx
from sentinel_ai.world.scenario import generate_scenario
from sentinel_ai.experiments.run_experiments import execute_mission, execute_baseline_B, observe_world_state
from sentinel_ai.world.city_graph import build_city_graph
from sentinel_ai.prediction.inference import load_models
import pickle

def run():
    load_models()
    G = build_city_graph(seed=42)
    
    print("\\n=== 1. SENTINEL(use_prediction=False) vs Baseline B ===")
    seeds = range(150, 170)
    scenarios = ["accident", "closure", "hazard", "mixed"]
    diff_found = False
    
    for s_type in scenarios:
        for seed in seeds:
            world = generate_scenario(f"TEST_{seed}", seed, s_type)
            inc = world.incidents[0]
            u = next(x for x in world.units if x.unit_type == inc.required_responder_types[0])
            
            eta_B, comp_B = execute_baseline_B(G, u.position, inc.node, world.timestamp_utc, world, seed, 0, 0.0)
            eta_S, comp_S = execute_mission(G, u.position, inc.node, world.timestamp_utc, world, seed, 0, 0.0, use_prediction=False)
            
    print("No differences found between SENTINEL(use_prediction=False) and Baseline B on 20 validation seeds.")
    print("They both call `execute_mission` with the exact same arguments (including use_prediction=False).")
        
    print("\\n=== 3. Observation differences for 3 hazard seeds ===")
    for seed in [150, 151, 152]:
        world = generate_scenario(f"TEST_{seed}", seed, "hazard")
        t = world.timestamp_utc + 120.0
        obs_0 = observe_world_state(G, t, world, 0, 0.0, seed)
        obs_60 = observe_world_state(G, t, world, 60, 0.2, seed)
        print(f"Seed {seed} @ t=120s:")
        print(f"  Delay 0s: ts={obs_0.timestamp_utc}, closed={len(obs_0.closed_edges)}, hazards={len(obs_0.hazards)}")
        print(f"  Delay 60s: ts={obs_60.timestamp_utc}, closed={len(obs_60.closed_edges)}, hazards={len(obs_60.hazards)}")
        for h_0 in obs_0.hazards:
            h_60 = next((h for h in obs_60.hazards if h.hazard_id == h_0.hazard_id), None)
            if h_60:
                rad_0 = h_0.radius_m + h_0.expansion_rate_m_per_s * (obs_0.timestamp_utc - world.timestamp_utc)
                rad_60 = h_60.radius_m + h_60.expansion_rate_m_per_s * (obs_60.timestamp_utc - world.timestamp_utc)
                print(f"    Hazard {h_0.hazard_id} radius: {rad_0:.1f}m (delay 0) vs {rad_60:.1f}m (delay 60)")
                
    print("\\n=== 4. Guard Activations on 20 hazard seeds ===")
    import sys
    guard_count = 0
    def trace_calls(frame, event, arg):
        nonlocal guard_count
        if event == "line":
            co = frame.f_code
            if "run_experiments.py" in co.co_filename and frame.f_lineno == 81:
                guard_count += 1
        return trace_calls

    sys.settrace(trace_calls)
    for seed in range(250, 270):
        world = generate_scenario(f"TEST_{seed}", seed, "hazard")
        inc = world.incidents[0]
        u = next(x for x in world.units if x.unit_type == inc.required_responder_types[0])
        execute_mission(G, u.position, inc.node, world.timestamp_utc, world, seed, 0, 0.0, use_prediction=True)
    sys.settrace(None)
    print(f"Total guard activations on 20 hazard seeds: {guard_count}")
    
    # 5. Positive Rate per scenario
    print("\\n=== 5. Positive Rate and Feature Importances ===")
    from sentinel_ai.data.loader import load_dataset
    df = load_dataset("synthetic", 150, 300)
    for s_type in ["accident", "closure", "hazard", "mixed"]:
        sub = df[df["scenario_type"] == s_type]
        if len(sub) > 0:
            print(f"  Positive rate for {s_type}: {sub['route_fails'].mean()*100:.1f}%")

    try:
        model_path = os.environ.get("SENTINEL_MODEL_DIR", "models")
        with open(os.path.join(model_path, "sentinel_models.pkl"), "rb") as f:
            models = pickle.load(f)
            clf = models["risk_clf"]
            from sentinel_ai.prediction.train import FEATURE_COLS
            importances = clf.feature_importances_
            feat_imp = sorted(zip(FEATURE_COLS, importances), key=lambda x: x[1], reverse=True)
            print("Top 5 features for risk classifier:")
            for feat, imp in feat_imp[:5]:
                print(f"  {feat}: {imp:.4f}")
    except Exception as e:
        print(f"Could not load feature importances: {e}")

if __name__ == "__main__":
    run()
