from typing import List, Dict, Any, Tuple
from sentinel_ai.contracts import WorldState, RouteCandidate
from sentinel_ai.config_loader import config

def compute_resilience(
    routes: List[RouteCandidate],
    world_state: WorldState,
    corridor_readiness: float = 0.5
) -> Tuple[float, Dict[str, float], List[str]]:
    """
    Computes an emergency resilience score (0-100) along with its components.
    This is a decision-support metric, not a safety certification.
    """
    weights = config["resilience"]["weights"]
    
    primary = next((r for r in routes if r.role == "primary"), None)
    backup = next((r for r in routes if r.role == "backup"), None)
    contingency = next((r for r in routes if r.role == "contingency"), None)
    
    components = {}
    reasons = []
    
    # 1. Route Redundancy
    redundancy_score = 0.0
    if primary: redundancy_score += 0.4
    if backup: redundancy_score += 0.4
    if contingency: redundancy_score += 0.2
    components["route_redundancy"] = redundancy_score * 100.0
    reasons.append(f"Route redundancy is {redundancy_score*100:.0f}% based on available diverse paths.")
    
    # 2. Backup Viability
    if backup and primary:
        backup_ratio = primary.eta_p50_min / max(0.1, backup.eta_p50_min)
        viability = min(1.0, backup_ratio * 1.2) # if backup is close to primary, it's good
    else:
        viability = 0.0
    components["backup_viability"] = viability * 100.0
    if viability > 0:
        reasons.append("Backup route is highly viable.")
    else:
        reasons.append("No viable backup route identified.")
        
    # 3. Failure Probability
    if primary:
        success_prob = max(0.0, 1.0 - primary.risk_probability)
    else:
        success_prob = 0.0
    components["failure_probability"] = success_prob * 100.0
    reasons.append(f"Primary route success probability is {success_prob*100:.0f}%.")
    
    # 4. Hazard Exposure
    # Assume 100% minus the exposure penalty
    components["hazard_exposure"] = 100.0
    if primary:
        # Assuming hazard penalty logic (simplified here)
        if primary.score > 10.0: # arbitrary threshold for demo
            components["hazard_exposure"] = max(0.0, 100.0 - (primary.score - 10.0)*5.0)
    reasons.append(f"Hazard exposure score is {components['hazard_exposure']:.0f}%.")
    
    # 5. Destination Availability
    total_hospitals = len(world_state.hospitals)
    avail_hospitals = sum(1 for h in world_state.hospitals if h.available and h.capacity > 0)
    dest_avail = avail_hospitals / max(1, total_hospitals)
    components["destination_availability"] = dest_avail * 100.0
    
    # 6. Resource Availability
    total_units = len(world_state.units)
    avail_units = sum(1 for u in world_state.units if u.available)
    res_avail = avail_units / max(1, total_units)
    components["resource_availability"] = res_avail * 100.0
    
    # 7. Corridor Readiness
    components["corridor_readiness"] = corridor_readiness * 100.0
    
    # Total Score
    total_score = sum(components[k] * weights[k] for k in weights)
    
    return total_score, components, reasons
