import networkx as nx
from typing import List, Dict, Any, Tuple
from sentinel_ai.contracts import WorldState, HospitalRanking
from sentinel_ai.routing.candidates import generate_and_score_routes

def rank_destinations(
    G: nx.DiGraph,
    origin: str,
    depart_time_utc: float,
    world_state: WorldState,
    required_capabilities: List[str]
) -> List[HospitalRanking]:
    """
    Ranks hospitals based on hard filters (capabilities, capacity, availability)
    and then by predicted ETA and route reliability.
    """
    rankings = []
    
    for hospital in world_state.hospitals:
        unsuitable_reasons = []
        suitable = True
        
        # Hard filters
        if not hospital.available:
            suitable = False
            unsuitable_reasons.append("Hospital is not available.")
            
        if hospital.capacity <= 0:
            suitable = False
            unsuitable_reasons.append("Hospital is at full capacity.")
            
        for cap in required_capabilities:
            if cap not in hospital.capabilities:
                suitable = False
                unsuitable_reasons.append(f"Required capability '{cap}' unavailable.")
                
        if not suitable:
            rankings.append(HospitalRanking(
                hospital_id=hospital.hospital_id,
                rank=None,
                suitable=False,
                unsuitable_reasons=unsuitable_reasons,
                predicted_eta_min=None,
                route_reliability_score=None
            ))
            continue
            
        # Get routing to hospital
        routes = generate_and_score_routes(G, origin, hospital.node, depart_time_utc, world_state)
        primary = next((r for r in routes if r.role == "primary"), None)
        
        if not primary:
            rankings.append(HospitalRanking(
                hospital_id=hospital.hospital_id,
                rank=None,
                suitable=False,
                unsuitable_reasons=["No viable route found to hospital."],
                predicted_eta_min=None,
                route_reliability_score=None
            ))
            continue
            
        rankings.append(HospitalRanking(
            hospital_id=hospital.hospital_id,
            rank=None,
            suitable=True,
            unsuitable_reasons=[],
            predicted_eta_min=primary.eta_p50_min,
            route_reliability_score=max(0.0, 1.0 - primary.risk_probability)
        ))
        
    # Sort suitable hospitals
    suitable_hospitals = [h for h in rankings if h.suitable]
    # Score for sorting: combine ETA and reliability (e.g., lower is better -> ETA - reliability * 10)
    suitable_hospitals.sort(key=lambda h: h.predicted_eta_min - (h.route_reliability_score * 5.0))
    
    # Assign ranks
    for i, h in enumerate(suitable_hospitals):
        h.rank = i + 1
        
    # Combine back
    unsuitable_hospitals = [h for h in rankings if not h.suitable]
    return suitable_hospitals + unsuitable_hospitals
