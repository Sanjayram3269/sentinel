"""Build simulation contexts only from explicit SENTINEL/provider mappings."""

import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.models import Mission, Route, TrafficSignal, Vehicle
from app.models.enums import RouteStatus
from app.schemas.simulation import (
    SignalDefinition,
    SimulationMode,
    SimulationRequest,
    SimulationScenario,
    TrafficFlow,
)


class ScenarioBuilder:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    async def build(
        self,
        db: AsyncSession,
        mission_id: UUID,
        mode: SimulationMode,
        request: SimulationRequest,
    ) -> SimulationScenario:
        mission = await db.get(Mission, mission_id)
        if mission is None:
            raise HTTPException(status_code=404, detail="Mission not found")
        vehicle = await db.get(Vehicle, request.vehicle_id)
        if vehicle is None:
            raise HTTPException(status_code=404, detail="Vehicle not found")
        if vehicle.mission_id != mission_id:
            raise HTTPException(status_code=409, detail="Vehicle does not belong to mission")
        route = await db.scalar(
            select(Route).where(
                Route.id == request.route_id,
                Route.mission_id == mission_id,
            )
        )
        if route is None:
            raise HTTPException(status_code=404, detail="Route not found")
        if route.vehicle_id != vehicle.id:
            raise HTTPException(status_code=409, detail="Route belongs to another vehicle")
        if route.status is not RouteStatus.ACTIVE:
            raise HTTPException(status_code=409, detail="Simulation requires an active route")
        if not request.route_edge_ids:
            raise HTTPException(status_code=422, detail="Route edge mapping is required")
        if len(set(request.route_edge_ids)) != len(request.route_edge_ids):
            raise HTTPException(status_code=422, detail="Route edge mapping contains duplicates")
        self._validate_network(request.network_id, request.route_edge_ids, request.traffic_flows)

        signals: list[SignalDefinition] = []
        if request.traffic_signal_ids:
            rows = (
                await db.scalars(
                    select(TrafficSignal)
                    .where(TrafficSignal.id.in_(request.traffic_signal_ids))
                    .order_by(TrafficSignal.id)
                    .limit(100)
                )
            ).all()
            if len(rows) != len(set(request.traffic_signal_ids)):
                raise HTTPException(status_code=404, detail="Traffic signal not found")
            for signal in rows:
                if not signal.enabled:
                    raise HTTPException(status_code=409, detail="Traffic signal is disabled")
                metadata = signal.signal_metadata
                try:
                    definition = SignalDefinition(
                        traffic_signal_id=signal.id,
                        signal_id=metadata["sumo_signal_id"],
                        edge_id=metadata["edge_id"],
                        valid_phases=metadata["valid_phases"],
                        preemption_phase=metadata["preemption_phase"],
                        release_phase=metadata["release_phase"],
                        safe_transitions=metadata["safe_transitions"],
                        maximum_duration_seconds=metadata["maximum_duration_seconds"],
                        initial_phase=metadata["initial_phase"],
                    )
                except (KeyError, TypeError, ValueError) as error:
                    raise HTTPException(
                        status_code=422,
                        detail="Traffic signal lacks valid SUMO phase and transition metadata",
                    ) from error
                if definition.edge_id not in request.route_edge_ids:
                    raise HTTPException(
                        status_code=422,
                        detail="Traffic signal is not mapped to the supplied route corridor",
                    )
                signals.append(definition)

        return SimulationScenario(
            mission_id=mission_id,
            vehicle_id=vehicle.id,
            route_id=route.id,
            mode=mode,
            network_id=request.network_id,
            route_edge_ids=request.route_edge_ids,
            traffic_signals=signals,
            traffic_flows=request.traffic_flows,
            emergency_vehicle_id=f"sentinel-{vehicle.id}",
            emergency_vehicle_configuration=request.emergency_vehicle_configuration,
            seed=request.seed,
            max_simulation_seconds=request.max_simulation_seconds,
            start_time=datetime.now(timezone.utc),
            correlation_id=request.correlation_id or uuid4(),
        )

    def _validate_network(
        self,
        network_id: str,
        route_edge_ids: list[str],
        traffic_flows: list[TrafficFlow],
    ) -> None:
        if network_id == self.settings.simulation_development_network_id:
            fixture = json.loads(
                Path(self.settings.simulation_fixture_path).read_text(encoding="utf-8")
            )
            if traffic_flows:
                raise HTTPException(
                    status_code=422,
                    detail="The fixed development fixture does not accept dynamic traffic flows",
                )
            known_edges = set(fixture["edge_ids"])
            if not set(route_edge_ids) <= known_edges:
                raise HTTPException(
                    status_code=422,
                    detail="Route edge mapping is not present in the development fixture",
                )
            for flow in traffic_flows:
                if not set(flow.edge_ids) <= known_edges:
                    raise HTTPException(
                        status_code=422,
                        detail="Traffic flow references an edge outside the development fixture",
                    )
            return

        if not self.settings.sumo_network_id or network_id != self.settings.sumo_network_id:
            raise HTTPException(status_code=422, detail="Simulation network mapping is unavailable")
