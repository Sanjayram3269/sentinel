import pandas as pd
import numpy as np
import networkx as nx
import os
import time
import random
from scipy import stats

from sentinel_ai.world.city_graph import build_city_graph
from sentinel_ai.world.scenario import generate_scenario
from sentinel_ai.world.simulator import get_edge_delay_s
from sentinel_ai.api import optimize_mission
from sentinel_ai.routing.candidates import generate_and_score_routes
from sentinel_ai.prediction.inference import load_models
from sentinel_ai.contracts import OptimizeMissionRequest, WorldState
import sentinel_ai.routing.resilience as resilience

def observe_world_state(G: nx.DiGraph, true_time: float, true_world: WorldState, delay_s: float, dropout_p: float, seed: int) -> WorldState:
    """Returns a delayed and dropped-out view of the world."""
    # Observe at (true_time - delay_s)
    obs_time = max(0.0, true_time - delay_s)
    
    r = random.Random(seed + int(true_time))
    
    # We figure out what the closures and hazards look like at obs_time
    # Actually, in this synthetic world, closures and hazards are static from their start_time.
    # We just apply dropout.
    obs = WorldState(
        timestamp_utc=obs_time,
        demand_level=true_world.demand_level,
        closed_edges=[],
        incidents=true_world.incidents,
        hazards=[],
        units=true_world.units,
        hospitals=true_world.hospitals
    )
    for ce in true_world.closed_edges:
        if r.random() > dropout_p: obs.closed_edges.append(ce)
    for h in true_world.hazards:
        if r.random() > dropout_p: obs.hazards.append(h)
        
    return obs

def get_observed_edge_delay_s(G, u, v, obs_time, obs_world, seed):
    # What the routing algorithm THINKS the delay is based on obs_world
    # We call get_edge_delay_s but passing the obs_world
    return get_edge_delay_s(G, u, v, obs_time, obs_world, seed)

def execute_baseline_B(G: nx.DiGraph, depart_node: str, target_node: str, depart_time: float, true_world, seed: int, delay_s: float, dropout_p: float):
    current_node = depart_node
    current_time = depart_time
    tt_sum = 0.0
    last_replan_time = depart_time - 60.0
    current_route = []
    
    G_reactive = G.copy()
    
    iters = 0
    while current_node != target_node:
        iters += 1
        if iters > 200:
            print("WARNING: Infinite loop in execute_baseline_B!")
            return float('inf'), False
            
        if current_time - last_replan_time >= 60.0 or not current_route:
            obs_world = observe_world_state(G, current_time, true_world, delay_s, dropout_p, seed)
            G_curr = G.copy()
            # Also apply known closures to G_curr
            for u, v in G.edges():
                if not G_reactive.has_edge(u, v):
                    if G_curr.has_edge(u, v): G_curr.remove_edge(u, v)
            
            for u, v in list(G_curr.edges()):
                tt_s, is_closed = get_observed_edge_delay_s(G, u, v, obs_world.timestamp_utc, obs_world, seed)
                if is_closed:
                    G_curr.remove_edge(u, v)
                else:
                    G_curr[u][v]["weight"] = tt_s
            try:
                new_path = nx.shortest_path(G_curr, source=current_node, target=target_node, weight="weight")
                if len(new_path) < 2: return tt_sum / 60.0, True
                current_route = [f"{new_path[i]}->{new_path[i+1]}" for i in range(len(new_path)-1)]
                last_replan_time = current_time
            except nx.NetworkXNoPath:
                return float('inf'), False
                
        edge_id = current_route.pop(0)
        u, v = edge_id.split("->")
        # TRUE travel
        tt_s, is_closed = get_edge_delay_s(G, u, v, current_time, true_world, seed)
        if is_closed:
            # Immediate reactive fallback
            try:
                if G_reactive.has_edge(u, v): G_reactive.remove_edge(u, v)
                new_path = nx.shortest_path(G_reactive, source=u, target=target_node, weight="length_m")
                current_route = [f"{new_path[i]}->{new_path[i+1]}" for i in range(len(new_path)-1)]
                continue
            except nx.NetworkXNoPath:
                return float('inf'), False
                
        tt_sum += tt_s
        current_time += tt_s
        current_node = v
        
    return tt_sum / 60.0, True

def execute_sentinel(G: nx.DiGraph, depart_node: str, target_node: str, depart_time: float, true_world, seed: int, delay_s: float, dropout_p: float):
    current_node = depart_node
    current_time = depart_time
    tt_sum = 0.0
    last_replan_time = depart_time - 60.0
    current_route = []
    
    num_replans = 0
    replan_latency = 0.0
    
    G_reactive = G.copy()
    
    iters = 0
    while current_node != target_node:
        iters += 1
        if iters > 200:
            print("WARNING: Infinite loop in execute_sentinel!")
            return float('inf'), False
            
        obs_world = observe_world_state(G, current_time, true_world, delay_s, dropout_p, seed)
        
        # Check if we need to replan
        needs_replan = False
        if not current_route or current_time - last_replan_time >= 60.0:
            needs_replan = True
        else:
            # Check observed closures on remaining route
            for edge_id in current_route:
                u, v = edge_id.split("->")
                if f"{u}->{v}" in obs_world.closed_edges:
                    needs_replan = True
                    break
            
            # Predict risk
            if not needs_replan:
                from sentinel_ai.prediction.inference import predict_risk
                fails, prob, _, _ = predict_risk(G, current_route, obs_world.timestamp_utc, obs_world)
                if prob > 0.8:
                    needs_replan = True
        
        if needs_replan:
            num_replans += 1
            st = time.time()
            routes_resp = generate_and_score_routes(G, current_node, target_node, obs_world.timestamp_utc, obs_world)
            replan_latency += time.time() - st
            if not routes_resp:
                return float('inf'), False
            # Pick best route
            best = min(routes_resp, key=lambda r: r.eta_p50_min)
            current_route = best.edges
            last_replan_time = current_time
            if not current_route: return tt_sum / 60.0, True
            
        edge_id = current_route.pop(0)
        u, v = edge_id.split("->")
        tt_s, is_closed = get_edge_delay_s(G, u, v, current_time, true_world, seed)
        if is_closed:
            # Dynamic fail, but we'll try reactive fallback like B for fairness
            try:
                if G_reactive.has_edge(u, v): G_reactive.remove_edge(u, v)
                new_path = nx.shortest_path(G_reactive, source=u, target=target_node, weight="length_m")
                current_route = [f"{new_path[i]}->{new_path[i+1]}" for i in range(len(new_path)-1)]
                continue
            except nx.NetworkXNoPath:
                return float('inf'), False
                
        tt_sum += tt_s
        current_time += tt_s
        current_node = v
        
    return tt_sum / 60.0, True

def is_infeasible(G, source, target, depart_time, world_state, seed):
    G_clair = G.copy()
    for u, v in list(G.edges()):
        _, is_closed = get_edge_delay_s(G, u, v, depart_time, world_state, seed)
        if is_closed: G_clair.remove_edge(u, v)
    try:
        nx.shortest_path(G_clair, source, target)
        return False
    except nx.NetworkXNoPath:
        return True

def mean_confidence_interval(data, confidence=0.95):
    a = 1.0 * np.array(data)
    n = len(a)
    if n == 0: return 0.0, 0.0
    if n == 1: return a[0], 0.0
    m, se = np.mean(a), stats.sem(a)
    h = se * stats.t.ppf((1 + confidence) / 2., n-1)
    return m, h

def evaluate_single_run(args):
    s_type, delay_s, drop_p, seed = args
    from sentinel_ai.prediction.inference import load_models
    load_models()
    scenario_id = f"EXP_{s_type}_{seed}"
    world_state = generate_scenario(scenario_id, seed, s_type)
    incident = world_state.incidents[0]
    req_type = incident.required_responder_types[0]
    unit = next((u for u in world_state.units if u.unit_type == req_type), None)
    
    if not unit: return None
    G = build_city_graph(seed=42)
    if is_infeasible(G, unit.position, incident.node, world_state.timestamp_utc, world_state, seed):
        return {"scenario_type": s_type, "delay": delay_s, "dropout": drop_p, "seed": seed, "infeasible": 1}
        
    eta_B, comp_B = execute_baseline_B(G, unit.position, incident.node, world_state.timestamp_utc, world_state, seed, delay_s, drop_p)
    eta_S, comp_S = execute_sentinel(G, unit.position, incident.node, world_state.timestamp_utc, world_state, seed, delay_s, drop_p)
    
    return {
        "scenario_type": s_type, "delay": delay_s, "dropout": drop_p, "seed": seed, "infeasible": 0,
        "comp_B": int(comp_B), "eta_B": eta_B if comp_B else np.nan,
        "comp_S": int(comp_S), "eta_S": eta_S if comp_S else np.nan
    }

def run_experiments(output_dir="sentinel_ai/experiments/output/"):
    os.makedirs(output_dir, exist_ok=True)
    load_models()
    scenarios = ["mixed", "accident", "hazard", "closure"]
    
    val_seeds = list(range(150, 160))
    print(f"Validation Seeds: {val_seeds[0]}-{val_seeds[-1]}")
    
    test_seeds = list(range(200, 250))
    print(f"Test Seeds: {test_seeds[0]}-{test_seeds[-1]}\n")
    
    delay_settings = [0, 60, 120]
    dropout_settings = [0.0, 0.2]
    
    tasks = []
    for s_type in scenarios:
        for delay_s in delay_settings:
            for drop_p in dropout_settings:
                for seed in test_seeds:
                    tasks.append((s_type, delay_s, drop_p, seed))
                    
    results = []
    total = len(tasks)
    import time
    start_t = time.time()
    for i, t in enumerate(tasks):
        if i % 10 == 0:
            print(f"[{time.time() - start_t:.1f}s] Progress: {i}/{total} tasks evaluated...")
        res = evaluate_single_run(t)
        if res: results.append(res)
                    
    df = pd.DataFrame(results)
    df.to_csv(os.path.join(output_dir, "closed_loop_results.csv"), index=False)
    
    print("\n--- FINAL PROTOCOL EXPERIMENT RESULTS (Mean ± 95% CI) ---")
    
    for s_type in scenarios:
        for delay_s in delay_settings:
            for drop_p in dropout_settings:
                sub = df[(df["scenario_type"] == s_type) & (df["delay"] == delay_s) & (df["dropout"] == drop_p)]
                inf_count = sub["infeasible"].sum()
                feas = sub[sub["infeasible"] == 0]
                n_feas = len(feas)
                
                c_B = feas["comp_B"].mean() * 100 if n_feas > 0 else 0
                c_S = feas["comp_S"].mean() * 100 if n_feas > 0 else 0
                
                pair = feas[(feas["comp_S"] == 1) & (feas["comp_B"] == 1)]
                m_S, ci_S = mean_confidence_interval(pair["eta_S"])
                m_B, ci_B = mean_confidence_interval(pair["eta_B"])
                m_diff, ci_diff = mean_confidence_interval(pair["eta_B"] - pair["eta_S"])
                
                print(f"[{s_type.upper()}] D={delay_s}s, Drp={drop_p:.1f} | N={n_feas} (Inf={inf_count}) | Comp B:{c_B:.1f}% S:{c_S:.1f}% | S:{m_S:.2f}±{ci_S:.2f}m vs B:{m_B:.2f}±{ci_B:.2f}m | S saved: {m_diff:.2f}±{ci_diff:.2f}m")

if __name__ == "__main__":
    run_experiments()
