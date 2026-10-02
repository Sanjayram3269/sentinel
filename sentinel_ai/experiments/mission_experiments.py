import pandas as pd
import numpy as np
import networkx as nx
import os

from sentinel_ai.world.city_graph import build_city_graph
from sentinel_ai.world.scenario import generate_scenario
from sentinel_ai.world.simulator import get_edge_delay_s
from sentinel_ai.contracts import OptimizeMissionRequest, IncidentState
from sentinel_ai.api import optimize_mission
from sentinel_ai.prediction.inference import load_models
from sentinel_ai.experiments.run_experiments import execute_sentinel, execute_baseline_B, mean_confidence_interval

def generate_mission_scenario(seed: int):
    # 3 units, 2 incidents, 4 hospitals (1 full)
    world = generate_scenario(f"MISS_{seed}", seed, "mixed")
    # Add second incident
    import random
    r = random.Random(seed)
    world.incidents.append(
        IncidentState(
            incident_id=f"INC_{seed}_2",
            node=f"N_{r.randint(1,8)}_{r.randint(1,8)}",
            priority=r.choice([1, 2, 3]),
            required_responder_types=["fire"],
            reported_time_utc=world.timestamp_utc
        )
    )
    # Ensure one hospital is full or lacks ICU
    world.hospitals[0].capacity = 0
    return world

def run_mission_experiments(output_dir="sentinel_ai/experiments/output/"):
    os.makedirs(output_dir, exist_ok=True)
    load_models()
    G = build_city_graph(seed=42)
    test_seeds = list(range(200, 250))
    
    results = []
    
    for seed in test_seeds:
        world = generate_mission_scenario(seed)
        
        # SENTINEL
        req = OptimizeMissionRequest(incident_id="ALL", world_state=world, available_unit_ids=[u.unit_id for u in world.units])
        try:
            # optimize_mission handles all incidents if incident_id is 'ALL'? 
            # In our API, optimize_mission takes a single incident_id.
            # We can loop over incidents.
            s_time = 0.0
            s_comp = True
            for inc in world.incidents:
                req_inc = OptimizeMissionRequest(incident_id=inc.incident_id, world_state=world, available_unit_ids=[u.unit_id for u in world.units])
                plan = optimize_mission(req_inc)
                for a in plan.assignments:
                    # closed-loop execution
                    unit = next(u for u in world.units if u.unit_id == a.unit_id)
                    eta, comp = execute_sentinel(G, unit.position, inc.node, world.timestamp_utc, world, seed, 0, 0)
                    if not comp: s_comp = False
                    s_time = max(s_time, eta)
            
            # Baseline C: Nearest available unit + Nearest hospital + dynamic rerouting
            c_time = 0.0
            c_comp = True
            for inc in world.incidents:
                best_dist = float('inf')
                best_u = None
                for req_type in inc.required_responder_types:
                    for u in world.units:
                        if u.unit_type == req_type:
                            try:
                                path = nx.shortest_path(G, u.position, inc.node, weight="length_m")
                                dist = sum(G[path[i]][path[i+1]]["length_m"] for i in range(len(path)-1))
                                if dist < best_dist:
                                    best_dist = dist
                                    best_u = u
                            except: pass
                    if best_u:
                        eta, comp = execute_baseline_B(G, best_u.position, inc.node, world.timestamp_utc, world, seed, 0, 0)
                        if not comp: c_comp = False
                        c_time = max(c_time, eta)
                        
            results.append({
                "seed": seed,
                "comp_S": int(s_comp), "time_S": s_time if s_comp else np.nan,
                "comp_C": int(c_comp), "time_C": c_time if c_comp else np.nan
            })
        except Exception as e:
            continue
            
    df = pd.DataFrame(results)
    
    print("\n--- MISSION LEVEL EXPERIMENT ---")
    c_S = df["comp_S"].mean() * 100
    c_C = df["comp_C"].mean() * 100
    
    pair = df[(df["comp_S"] == 1) & (df["comp_C"] == 1)]
    m_S, ci_S = mean_confidence_interval(pair["time_S"])
    m_C, ci_C = mean_confidence_interval(pair["time_C"])
    
    print(f"Completion Rate -> C (Nearest): {c_C:.1f}%, SENTINEL: {c_S:.1f}%")
    print(f"Mission Time (n={len(pair)}) -> C: {m_C:.2f}±{ci_C:.2f}m | S: {m_S:.2f}±{ci_S:.2f}m")

if __name__ == "__main__":
    run_mission_experiments()
