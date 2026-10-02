"""PostGIS/Redis simulation API integration tests using FakeSimulationAdapter."""

import asyncio
import os
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from geoalchemy2 import WKTElement
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.main import app
from app.models import Route, TrafficSignal
from app.models.enums import RouteStatus
from app.services.simulation.fake_adapter import FakeSimulationAdapter

pytestmark = pytest.mark.integration
TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
DEVELOPMENT_NETWORK_ID = "sentinel-development-test-network"
SIGNAL_METADATA = {
    "sumo_signal_id": "dev-tls-1",
    "edge_id": "dev-edge-1",
    "valid_phases": ["R", "G", "Y"],
    "initial_phase": "R",
    "preemption_phase": "G",
    "release_phase": "R",
    "safe_transitions": {"R": ["G"], "G": ["R"], "Y": ["R"]},
    "maximum_duration_seconds": 2,
}


def create_mission_and_route(
    client: TestClient, name: str
) -> tuple[str, str, str, str]:
    mission = client.post("/api/v1/missions", json={"objective": name})
    assert mission.status_code == 201, mission.text
    mission_id = mission.json()["id"]
    vehicle = client.post(
        "/api/v1/vehicles",
        json={
            "mission_id": mission_id,
            "vehicle_type": "AMBULANCE",
            "call_sign": f"SIM-{uuid4()}",
        },
    )
    assert vehicle.status_code == 201, vehicle.text
    route_id = uuid4()
    signal_id = uuid4()

    async def persist() -> None:
        engine = create_async_engine(TEST_DATABASE_URL, pool_pre_ping=True)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with factory() as session:
                async with session.begin():
                    session.add(
                        Route(
                            id=route_id,
                            mission_id=UUID(mission_id),
                            vehicle_id=UUID(vehicle.json()["id"]),
                            status=RouteStatus.ACTIVE,
                            name="Simulation test route",
                            geometry=WKTElement(
                                "LINESTRING(77.59 12.97, 77.60 12.98)", srid=4326
                            ),
                            distance_meters=1000,
                            estimated_duration_seconds=100,
                            risk_score=0.1,
                        )
                    )
                    session.add(
                        TrafficSignal(
                            id=signal_id,
                            external_id=f"dev-{signal_id}",
                            location=WKTElement("POINT(77.60 12.98)", srid=4326),
                            current_phase="R",
                            signal_metadata=SIGNAL_METADATA,
                            enabled=True,
                        )
                    )
        finally:
            await engine.dispose()

    asyncio.run(persist())
    return mission_id, vehicle.json()["id"], str(route_id), str(signal_id)


def request_payload(vehicle_id: str, route_id: str, signal_id: str) -> dict[str, object]:
    return {
        "vehicle_id": vehicle_id,
        "route_id": route_id,
        "network_id": DEVELOPMENT_NETWORK_ID,
        "route_edge_ids": ["dev-edge-0", "dev-edge-1"],
        "traffic_signal_ids": [signal_id],
        "traffic_flows": [],
        "emergency_vehicle_configuration": {"sumo_vehicle_type_id": "emergency"},
        "seed": 101,
        "max_simulation_seconds": 30,
    }


def test_baseline_clearpath_comparison_persistence_and_events() -> None:
    with TestClient(app) as client:
        mission_id, vehicle_id, route_id, signal_id = create_mission_and_route(
            client, "Simulation paired comparison"
        )
        payload = request_payload(vehicle_id, route_id, signal_id)
        baseline = client.post(
            f"/api/v1/missions/{mission_id}/simulations/baseline", json=payload
        )
        assert baseline.status_code == 201, baseline.text
        baseline_body = baseline.json()
        assert baseline_body["mode"] == "BASELINE"
        assert baseline_body["status"] == "COMPLETED"
        assert baseline_body["simulator"] == "fake_simulation_adapter"
        assert baseline_body["metadata"]["network_id"] == DEVELOPMENT_NETWORK_ID
        assert "TEST NETWORK ONLY" in baseline_body["metadata"]["fixture_label"]
        assert baseline_body["clearpath_actions"] == []
        assert baseline_body["metrics"]["route_completed"] is True
        assert baseline_body["metrics"]["emergency_vehicle_travel_time_seconds"] == 4
        assert baseline_body["metrics"]["background_vehicle_throughput"] is None

        clearpath = client.post(
            f"/api/v1/missions/{mission_id}/simulations/clearpath", json=payload
        )
        assert clearpath.status_code == 201, clearpath.text
        clearpath_body = clearpath.json()
        assert clearpath_body["mode"] == "CLEARPATH"
        assert clearpath_body["clearpath_actions"][0]["decision"] == "APPROVED"
        assert clearpath_body["clearpath_actions"][0]["released_at_seconds"] == 3

        captured_scenarios = []
        previous_factory = getattr(app.state, "simulation_adapter_factory", None)
        app.state.simulation_adapter_factory = lambda simulation_scenario: (
            captured_scenarios.append(
                simulation_scenario.model_dump(mode="json", exclude={"mode"})
            )
            or FakeSimulationAdapter()
        )
        try:
            comparison = client.post(
                f"/api/v1/missions/{mission_id}/simulations/compare", json=payload
            )
        finally:
            if previous_factory is None:
                delattr(app.state, "simulation_adapter_factory")
            else:
                app.state.simulation_adapter_factory = previous_factory
        assert comparison.status_code == 201, comparison.text
        assert len(captured_scenarios) == 2
        assert captured_scenarios[0] == captured_scenarios[1]
        comparison_body = comparison.json()
        assert comparison_body["baseline"]["seed"] == comparison_body["clearpath"]["seed"] == 101
        assert comparison_body["baseline"]["metrics"] == comparison_body["clearpath"]["metrics"]
        assert comparison_body["comparison"]["travel_time_delta_seconds"] == 0
        assert comparison_body["comparison"]["travel_time_improvement_percent"] == 0
        assert comparison_body["comparison"]["stops_delta"] == 0

        fetched = client.get(
            f"/api/v1/missions/{mission_id}/simulations/{baseline_body['simulation_id']}"
        )
        assert fetched.status_code == 200
        wrong_mission = client.get(
            f"/api/v1/missions/{uuid4()}/simulations/{baseline_body['simulation_id']}"
        )
        assert wrong_mission.status_code == 404
        history = client.get(
            f"/api/v1/missions/{mission_id}/simulations?mode=CLEARPATH&limit=10"
        )
        assert history.status_code == 200
        assert len(history.json()["items"]) == 2
        assert all(item["mode"] == "CLEARPATH" for item in history.json()["items"])
        failures = client.get(
            f"/api/v1/missions/{mission_id}/simulations?status=FAILED&limit=10"
        )
        assert failures.status_code == 200
        assert failures.json()["items"] == []

        events = client.get(f"/api/v1/missions/{mission_id}/events?limit=100")
        assert events.status_code == 200
        run_events = [
            event
            for event in events.json()["items"]
            if event["payload"].get("simulation_run_id") == clearpath_body["simulation_id"]
        ]
        assert {event["event_type"] for event in run_events} >= {
            "SIMULATION_STARTED",
            "CLEARPATH_REQUESTED",
            "CLEARPATH_UPDATED",
            "SIMULATION_COMPLETED",
        }
        assert all(
            event["correlation_id"] == clearpath_body["correlation_id"]
            for event in run_events
        )


def test_invalid_scenario_mapping_is_rejected_without_network_fabrication() -> None:
    with TestClient(app) as client:
        mission_id, vehicle_id, route_id, signal_id = create_mission_and_route(
            client, "Invalid simulation mapping"
        )
        invalid_network = request_payload(vehicle_id, route_id, signal_id)
        invalid_network["network_id"] = "unknown-network"
        response = client.post(
            f"/api/v1/missions/{mission_id}/simulations/baseline", json=invalid_network
        )
        assert response.status_code == 422

        invalid_edge = request_payload(vehicle_id, route_id, signal_id)
        invalid_edge["route_edge_ids"] = ["invented-road-edge"]
        response = client.post(
            f"/api/v1/missions/{mission_id}/simulations/baseline", json=invalid_edge
        )
        assert response.status_code == 422

        dynamic_flows = request_payload(vehicle_id, route_id, signal_id)
        dynamic_flows["traffic_flows"] = [
            {
                "demand_id": "request-flow",
                "edge_ids": ["dev-edge-0"],
                "vehicle_count": 1,
                "depart_period_seconds": 1,
            }
        ]
        response = client.post(
            f"/api/v1/missions/{mission_id}/simulations/baseline", json=dynamic_flows
        )
        assert response.status_code == 422

        missing_mission = client.post(
            f"/api/v1/missions/{uuid4()}/simulations/baseline",
            json=request_payload(vehicle_id, route_id, signal_id),
        )
        assert missing_mission.status_code == 404

        other_mission_id, other_vehicle_id, _, _ = create_mission_and_route(
            client, "Other simulation owner"
        )
        wrong_owner = client.post(
            f"/api/v1/missions/{mission_id}/simulations/baseline",
            json=request_payload(other_vehicle_id, route_id, signal_id),
        )
        assert wrong_owner.status_code == 409

        import asyncio
        from sqlalchemy import update
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
        from app.models import Route

        async def deactivate_route() -> None:
            engine = create_async_engine(TEST_DATABASE_URL, pool_pre_ping=True)
            factory = async_sessionmaker(engine, expire_on_commit=False)
            try:
                async with factory() as session:
                    async with session.begin():
                        await session.execute(
                            update(Route)
                            .where(Route.id == UUID(route_id))
                            .values(status="CANDIDATE")
                        )
            finally:
                await engine.dispose()

        asyncio.run(deactivate_route())
        inactive_route = client.post(
            f"/api/v1/missions/{mission_id}/simulations/baseline",
            json=request_payload(vehicle_id, route_id, signal_id),
        )
        assert inactive_route.status_code == 409

        async def reactivate_route() -> None:
            engine = create_async_engine(TEST_DATABASE_URL, pool_pre_ping=True)
            factory = async_sessionmaker(engine, expire_on_commit=False)
            try:
                async with factory() as session:
                    async with session.begin():
                        await session.execute(
                            update(Route)
                            .where(Route.id == UUID(route_id))
                            .values(status="ACTIVE")
                        )
            finally:
                await engine.dispose()

        asyncio.run(reactivate_route())

        missing_signal = request_payload(vehicle_id, route_id, str(uuid4()))
        response = client.post(
            f"/api/v1/missions/{mission_id}/simulations/clearpath", json=missing_signal
        )
        assert response.status_code == 404
        missing_route = request_payload(vehicle_id, str(uuid4()), signal_id)
        response = client.post(
            f"/api/v1/missions/{mission_id}/simulations/baseline", json=missing_route
        )
        assert response.status_code == 404


def test_failed_adapter_persists_failed_run_and_event() -> None:
    with TestClient(app) as client:
        mission_id, vehicle_id, route_id, signal_id = create_mission_and_route(
            client, "Simulation adapter failure"
        )
        previous = getattr(app.state, "simulation_adapter_factory", None)
        app.state.simulation_adapter_factory = lambda scenario: FakeSimulationAdapter(
            fail_on_step=1
        )
        try:
            response = client.post(
                f"/api/v1/missions/{mission_id}/simulations/baseline",
                json=request_payload(vehicle_id, route_id, signal_id),
            )
        finally:
            if previous is None:
                delattr(app.state, "simulation_adapter_factory")
            else:
                app.state.simulation_adapter_factory = previous

        assert response.status_code == 201, response.text
        body = response.json()
        assert body["status"] == "FAILED"
        assert body["error_code"] == "SIMULATION_FAILURE"
        events = client.get(
            f"/api/v1/missions/{mission_id}/events?event_type=SIMULATION_FAILED"
        )
        assert events.status_code == 200
        assert any(
            event["payload"].get("simulation_run_id") == body["simulation_id"]
            for event in events.json()["items"]
        )
        failed_history = client.get(
            f"/api/v1/missions/{mission_id}/simulations?status=FAILED"
        )
        assert len(failed_history.json()["items"]) == 1


def test_safe_guard_rejects_phase_without_transition_metadata() -> None:
    with TestClient(app) as client:
        mission_id, vehicle_id, route_id, signal_id = create_mission_and_route(
            client, "Unsafe simulated phase"
        )

        import asyncio
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
        from sqlalchemy import select
        from app.models import TrafficSignal

        async def remove_safe_transition() -> None:
            engine = create_async_engine(TEST_DATABASE_URL, pool_pre_ping=True)
            factory = async_sessionmaker(engine, expire_on_commit=False)
            try:
                async with factory() as session:
                    async with session.begin():
                        signal = await session.scalar(
                            select(TrafficSignal).where(TrafficSignal.id == UUID(signal_id))
                        )
                        assert signal is not None
                        signal.signal_metadata = {
                            **SIGNAL_METADATA,
                            "safe_transitions": {"R": [], "G": ["R"], "Y": ["R"]},
                        }
            finally:
                await engine.dispose()

        asyncio.run(remove_safe_transition())
        response = client.post(
            f"/api/v1/missions/{mission_id}/simulations/clearpath",
            json=request_payload(vehicle_id, route_id, signal_id),
        )
        assert response.status_code == 201, response.text
        result = response.json()["clearpath_actions"][0]
        assert result["decision"] == "REJECTED"
        assert result["reason_code"] == "UNSAFE_PHASE_TRANSITION"
