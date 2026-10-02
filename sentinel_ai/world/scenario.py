import random
from typing import List, Dict, Any
from sentinel_ai.contracts import WorldState, UnitState, HospitalState, IncidentState, HazardState
import time

def generate_scenario(scenario_id: str, seed: int, scenario_type: str = "mixed") -> WorldState:
    """Generates a reproducible scenario with fixed seed."""
    seed_val = hash(f"{seed}_{scenario_type}") % (2**32)
    random.seed(seed_val)
    
    # 4 Hospitals
    hospitals = [
        HospitalState(hospital_id="H1", node="N_1_1", capacity=20, available=True, capabilities=["ICU", "trauma"]),
        HospitalState(hospital_id="H2", node="N_8_8", capacity=15, available=True, capabilities=["trauma"]),
        HospitalState(hospital_id="H3", node="N_2_8", capacity=10, available=True, capabilities=["burn"]),
        HospitalState(hospital_id="H4", node="N_8_2", capacity=5, available=True, capabilities=["ICU"]),
    ]
    
    # 3 Emergency Units
    units = [
        UnitState(unit_id="U1", unit_type="ambulance", position="N_0_0", available=True),
        UnitState(unit_id="U2", unit_type="fire", position="N_9_0", available=True),
        UnitState(unit_id="U3", unit_type="police", position="N_0_9", available=True),
    ]
    
    # Fixed base timestamp to avoid test failures
    base_timestamp = 1672531200.0 # 2023-01-01
    timestamp_utc = base_timestamp + random.uniform(0, 86400)
    demand_level = random.uniform(0.5, 1.5)
    
    incidents = []
    hazards = []
    closed_edges = []
    
    r_inc = random.Random(seed_val + 1)
    r_haz = random.Random(seed_val + 2)
    r_cls = random.Random(seed_val + 3)
    
    # Always generate at least one incident
    incidents.append(
        IncidentState(
            incident_id=f"INC_{scenario_id}_1",
            node=f"N_{r_inc.randint(3,7)}_{r_inc.randint(3,7)}",
            priority=r_inc.randint(1, 3),
            required_responder_types=["ambulance", "police"]
        )
    )
        
    if scenario_type == "hazard" or scenario_type == "mixed":
        hazards.append(
            HazardState(
                hazard_id=f"HAZ_{scenario_id}_1",
                center_node=f"N_{r_haz.randint(1,8)}_{r_haz.randint(1,8)}",
                radius_m=r_haz.uniform(100, 300),
                expansion_rate_m_per_s=r_haz.uniform(0.1, 0.5)
            )
        )
        
    if scenario_type == "closure" or scenario_type == "mixed":
        u_x, u_y = r_cls.randint(2, 7), r_cls.randint(2, 7)
        v_x, v_y = u_x + 1, u_y
        closed_edges.append(f"N_{u_x}_{u_y}->N_{v_x}_{v_y}")
        closed_edges.append(f"N_{v_x}_{v_y}->N_{u_x}_{u_y}")
        
    return WorldState(
        timestamp_utc=timestamp_utc,
        demand_level=demand_level,
        closed_edges=closed_edges,
        incidents=incidents,
        hazards=hazards,
        units=units,
        hospitals=hospitals
    )
