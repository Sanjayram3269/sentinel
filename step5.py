import os
import networkx as nx
from sentinel_ai.world.city_graph import build_city_graph
from sentinel_ai.world.scenario import generate_scenario
from sentinel_ai.optimization.optimizer import optimize_mission, greedy_mission_optimizer
from sentinel_ai.experiments.run_experiments import execute_mission
from sentinel_ai.prediction.inference import load_models

def run_mission():
    load_models()
    G = build_city_graph(seed=42)
    # We use a seed that generates a mixed scenario
    world = generate_scenario("STEP5", 42, "mixed")
    # Tweak the world to have exactly 1 incident requiring all 3
    world.incidents = [world.incidents[0]]
    world.incidents[0].required_responder_types = ["ambulance", "fire", "police"]
    
    # 4 hospitals. Make one full, one lacking trauma.
    # H1: full
    world.hospitals[0].available = False
    # H2: lacking trauma
    world.hospitals[1].capabilities = ["burn"] 
    # H3: has trauma, available
    world.hospitals[2].capabilities = ["trauma"]
    # H4: has trauma, available
    world.hospitals[3].capabilities = ["trauma"]
    
    available_units = [u.unit_id for u in world.units]
    
    # Sentinel CP-SAT
    plan_S = optimize_mission(G, world.incidents[0].incident_id, world, available_units)
    # Baseline C (Greedy)
    plan_B = greedy_mission_optimizer(G, world.incidents[0], world, [u for u in world.units], world.timestamp_utc)
    
    def simulate_plan(plan, use_pred):
        unit_results = {}
        hospital = None
        
        fail_causes = []
        for assign in plan.assignments:
            unit = next(u for u in world.units if u.unit_id == assign.unit_id)
            eta1, comp1 = execute_mission(G, unit.position, world.incidents[0].node, world.timestamp_utc, world, 42, 0, 0.0, use_prediction=use_pred)
            
            if not comp1:
                fail_causes.append(f"{unit.unit_id} failed to reach incident")
                unit_results[unit.unit_id] = {"comp": False, "time": float('inf')}
                continue
                
            if unit.unit_type == "ambulance":
                from sentinel_ai.hospital.ranking import rank_destinations
                hospitals = rank_destinations(G, world.incidents[0].node, world.timestamp_utc + eta1*60, world, ["trauma"])
                best_h = next((h for h in hospitals if h.suitable), None)
                if best_h:
                    hospital = best_h.hospital_id
                    eta2, comp2 = execute_mission(G, world.incidents[0].node, next(h.node for h in world.hospitals if h.hospital_id == hospital), world.timestamp_utc + eta1*60, world, 42, 0, 0.0, use_prediction=use_pred)
                    if not comp2:
                        fail_causes.append(f"{unit.unit_id} failed to reach hospital")
                        unit_results[unit.unit_id] = {"comp": False, "time": float('inf')}
                    else:
                        unit_results[unit.unit_id] = {"comp": True, "time": eta1 + eta2}
                else:
                    fail_causes.append("No suitable hospital found")
                    unit_results[unit.unit_id] = {"comp": False, "time": float('inf')}
            else:
                unit_results[unit.unit_id] = {"comp": True, "time": eta1}
                
        mission_comp = all(v["comp"] for v in unit_results.values())
        mission_time = max([v["time"] for v in unit_results.values()]) if mission_comp else float('inf')
        
        # Check hospital suitability
        suit_rate = 1.0
        if hospital:
            h_state = next((h for h in world.hospitals if h.hospital_id == hospital), None)
            if not h_state or not h_state.available or "trauma" not in h_state.capabilities: # assuming trauma required
                suit_rate = 0.0
        elif any(u.unit_type == "ambulance" for u in world.units if u.unit_id in [a.unit_id for a in plan.assignments]):
            suit_rate = 0.0
                
        return unit_results, mission_comp, mission_time, suit_rate, fail_causes

    res_S = simulate_plan(plan_S, use_pred=False) # prediction disabled per step 4
    res_B = simulate_plan(plan_B, use_pred=False)
    
    print("\\n--- MISSION LEVEL RESULTS ---")
    print("BASELINE C:")
    print(f"Per-unit: {res_B[0]}")
    print(f"Mission Comp: {res_B[1]}, Time: {res_B[2]:.2f}")
    print(f"Hospital Suitability: {res_B[3]}")
    print(f"Fail Causes: {res_B[4]}")
    
    print("\\nSENTINEL:")
    print(f"Per-unit: {res_S[0]}")
    print(f"Mission Comp: {res_S[1]}, Time: {res_S[2]:.2f}")
    print(f"Hospital Suitability: {res_S[3]}")
    print(f"Fail Causes: {res_S[4]}")

if __name__ == "__main__":
    run_mission()
