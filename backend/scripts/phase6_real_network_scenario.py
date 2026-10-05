"""Measured Phase 6 scenario over the real imported osm_urban_v1 network.

Run manually; not part of the test suite. Reports what the optimizer actually
selected and how long each stage took, against real PostGIS road data.
"""

import time
from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import app

# Real graph nodes used throughout Phases 3-5 verification.
ORIGIN = {"latitude": 13.093527834840323, "longitude": 77.58245818428705}
DESTINATION = {"latitude": 13.104091893053065, "longitude": 77.59160758950961}


def main() -> None:
    with TestClient(app) as client:
        mission_id = client.post(
            "/api/v1/missions", json={"objective": "Phase 6 real-network scenario"}
        ).json()["id"]
        client.post(
            "/api/v1/incidents",
            json={
                "mission_id": mission_id,
                "type": "MEDICAL",
                "severity": 4,
                "description": "Road traffic collision with suspected trauma",
                "location": ORIGIN,
            },
        )
        vehicle_id = client.post(
            "/api/v1/vehicles",
            json={
                "mission_id": mission_id,
                "vehicle_type": "AMBULANCE",
                "call_sign": f"SC6-{uuid4()}",
                "speed": 8.0,
            },
        ).json()["id"]

        started = time.perf_counter()
        generated = client.post(
            f"/api/v1/missions/{mission_id}/routes/candidates",
            json={
                "vehicle_id": vehicle_id,
                "origin": ORIGIN,
                "destination": DESTINATION,
            },
        ).json()
        routing_ms = (time.perf_counter() - started) * 1000
        print(f"Phase 5 route generation: {routing_ms:.1f} ms")
        for item in generated["candidates"]:
            print(
                f"  {item['resilience_role'] or 'UNASSIGNED':<12}"
                f" eta={item['estimated_duration_seconds']:>4}s"
                f" dist={item['distance_meters']:>8.1f}m"
                f" segs={len(item['road_segment_ids']):>3}"
                f" score={item['score']}"
            )

        started = time.perf_counter()
        plan = client.post(
            f"/api/v1/missions/{mission_id}/plans/optimize",
            json={
                "required_capabilities": ["trauma"],
                "required_vehicle_types": ["AMBULANCE"],
            },
        ).json()
        optimize_ms = (time.perf_counter() - started) * 1000

        payload = plan["plan_payload"]
        print(f"\nPhase 6 mission optimization: {optimize_ms:.1f} ms")
        print(f"  feasible            : {plan['feasible']}")
        print(f"  objective           : {plan['objective']}")
        print(f"  score (lower=better): {plan['score']}")
        print(f"  score coverage      : {plan['score_coverage']}")
        print(f"  selected hospital   : {payload['hospital']['name']}")
        print(f"  selected route role : {payload['route']['resilience_role']}")
        print(f"  selected route ETA  : {payload['route']['eta_seconds']} s")
        print(f"  selected route dist : {payload['route']['distance_meters']} m")
        print(
            f"  canonical edge ids  : {payload['route']['canonical_road_edge_count']}"
            f" (RoadEdge.id values, verbatim from Phase 5)"
        )
        print(f"  selected resources  : {len(plan['selected_resource_ids'])}")
        for item in payload["resources"]["selected"]:
            print(
                f"    {item['call_sign']} -> {item['requirement']}"
                f" ({item['vehicle_type']}, {item['status']},"
                f" {item['straight_line_response_meters']} m)"
            )
        print(f"  rejected resources  : {len(payload['resources']['rejected'])}")
        print(f"  options considered  : {payload['options_considered']}")
        print("\n--- RATIONALE ---")
        print(plan["rationale"])

        repeat = client.post(
            f"/api/v1/missions/{mission_id}/plans/optimize",
            json={
                "required_capabilities": ["trauma"],
                "required_vehicle_types": ["AMBULANCE"],
            },
        ).json()
        print("\n--- DETERMINISM ---")
        print(f"  same hospital : {repeat['selected_hospital_id'] == plan['selected_hospital_id']}")
        print(f"  same route    : {repeat['selected_route_id'] == plan['selected_route_id']}")
        print(f"  same score    : {repeat['score'] == plan['score']}")
        print(f"  same payload  : {repeat['plan_payload']['selected'] == payload['selected']}")

        what_if = client.post(
            f"/api/v1/missions/{mission_id}/plans/whatif",
            json={
                "component": "HOSPITAL",
                "component_id": plan["selected_hospital_id"],
                "baseline_plan_id": plan["plan_id"],
                "required_capabilities": ["trauma"],
                "required_vehicle_types": ["AMBULANCE"],
            },
        ).json()
        print("\n--- WHAT-IF (selected hospital removed) ---")
        print(f"  recomputed feasible : {what_if['recomputed_feasible']}")
        print(f"  plan changed        : {what_if['plan_changed']}")
        print(f"  explanation         : {what_if['explanation']}")


if __name__ == "__main__":
    main()
