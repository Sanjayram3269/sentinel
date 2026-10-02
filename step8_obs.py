import hashlib
from sentinel_ai.world.scenario import generate_scenario
from sentinel_ai.experiments.run_experiments import observe_world_state
from sentinel_ai.world.city_graph import build_city_graph

with open("config/default.yaml", "rb") as f:
    cfg_hash = hashlib.md5(f.read()).hexdigest()

print(f"CONFIG HASH: {cfg_hash}\\n")
print("--- OBSERVATION MODEL CONTENT DIFFERENCES ---")

G = build_city_graph(seed=42)
for seed in [250, 251, 252]:
    world = generate_scenario(f"TEST_{seed}", seed, "hazard")
    # Simulate time t=120
    t = world.timestamp_utc + 120.0
    obs_0 = observe_world_state(G, t, world, 0, 0.0, seed)
    obs_60 = observe_world_state(G, t, world, 60, 0.2, seed)
    
    print(f"Seed {seed} @ t=120s:")
    print(f"  Delay 0s: ts={obs_0.timestamp_utc}, hazards={len(obs_0.hazards)}")
    print(f"  Delay 60s: ts={obs_60.timestamp_utc}, hazards={len(obs_60.hazards)}")
    
    # Check radii
    for h_0 in obs_0.hazards:
        h_60 = next((h for h in obs_60.hazards if h.hazard_id == h_0.hazard_id), None)
        if h_60:
            # wait, hazards in synthetic world are static objects with expansion_rate_m_per_s. 
            # So the object itself is identical, the actual radius is computed at query time using timestamp_utc!
            radius_0 = h_0.radius_m + h_0.expansion_rate_m_per_s * (obs_0.timestamp_utc - world.timestamp_utc)
            radius_60 = h_60.radius_m + h_60.expansion_rate_m_per_s * (obs_60.timestamp_utc - world.timestamp_utc)
            print(f"    Hazard {h_0.hazard_id} calculated radius: {radius_0:.1f}m (delay 0) vs {radius_60:.1f}m (delay 60)")
    print()
