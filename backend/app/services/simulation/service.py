"""Simulation persistence, execution lifecycle, events, and comparisons."""

import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.models import Mission, SimulationRun
from app.models.enums import SimulationStatus
from app.schemas.events import EventCreate, EventType
from app.schemas.simulation import (
    SafetyDecision,
    SignalActionResult,
    SimulationComparisonMetrics,
    SimulationComparisonRead,
    SimulationHistory,
    SimulationMetrics,
    SimulationMode,
    SimulationRequest,
    SimulationRunRead,
    SimulationScenario,
)
from app.services.event_service import EventService
from app.services.simulation.adapter import (
    SimulationAdapter,
    SimulationAdapterError,
    adapter_metadata,
)
from app.services.simulation.fake_adapter import FakeSimulationAdapter
from app.services.simulation.runner import AdapterFactory, SimulationExecution, SimulationRunner
from app.services.simulation.scenario_builder import ScenarioBuilder
from app.services.simulation.sumo_adapter import SumoTraCIAdapter

MAX_SIMULATION_HISTORY = 100
logger = logging.getLogger(__name__)


def default_adapter_factory(settings: Settings) -> AdapterFactory:
    def create(scenario: SimulationScenario) -> SimulationAdapter:
        if scenario.network_id == settings.simulation_development_network_id:
            return FakeSimulationAdapter(fixture_path=Path(settings.simulation_fixture_path))
        return SumoTraCIAdapter(settings)

    return create


def _correlation_id(configuration: dict[str, Any]) -> UUID:
    try:
        return UUID(configuration["correlation_id"])
    except (KeyError, TypeError, ValueError):
        logger.error("Simulation run is missing a usable correlation ID")
        return uuid4()


def _mode(configuration: dict[str, Any]) -> SimulationMode:
    try:
        return SimulationMode(configuration.get("mode", SimulationMode.BASELINE))
    except ValueError:
        logger.error("Simulation run stored an unknown mode; reporting BASELINE")
        return SimulationMode.BASELINE


def simulation_run_read(run: SimulationRun) -> SimulationRunRead:
    configuration = run.configuration or {}
    metric_values = run.metrics
    clearpath_actions = configuration.get("clearpath_actions", [])
    return SimulationRunRead(
        simulation_id=run.id,
        mission_id=run.mission_id,
        mode=_mode(configuration),
        status=run.status,
        seed=run.scenario_seed,
        network_id=configuration.get("network_id"),
        simulator=run.simulator,
        metadata=configuration.get("adapter_metadata", {}),
        metrics=(SimulationMetrics.model_validate(metric_values) if metric_values else None),
        clearpath_actions=[SignalActionResult.model_validate(item) for item in clearpath_actions],
        correlation_id=_correlation_id(configuration),
        started_at=run.started_at,
        completed_at=run.finished_at,
        created_at=run.created_at,
        simulation_only=bool(configuration.get("simulation_only", True)),
        error_code=configuration.get("error_code"),
        error_message=configuration.get("error_message"),
    )


def build_comparison_metrics(
    baseline: SimulationMetrics | None, clearpath: SimulationMetrics | None
) -> SimulationComparisonMetrics:
    """Compute deltas from two measured runs.

    Every value is ``None`` when it cannot be derived from both runs. CLEARPATH
    may improve, be neutral, or degrade; no direction is assumed.
    """
    return SimulationComparisonMetrics(
        travel_time_delta_seconds=_delta(
            baseline.emergency_vehicle_travel_time_seconds if baseline else None,
            clearpath.emergency_vehicle_travel_time_seconds if clearpath else None,
        ),
        travel_time_improvement_percent=_improvement_percent(
            baseline.emergency_vehicle_travel_time_seconds if baseline else None,
            clearpath.emergency_vehicle_travel_time_seconds if clearpath else None,
        ),
        stopped_time_delta_seconds=_delta(
            baseline.stopped_time_seconds if baseline else None,
            clearpath.stopped_time_seconds if clearpath else None,
        ),
        stops_delta=_int_delta(
            baseline.number_of_stops if baseline else None,
            clearpath.number_of_stops if clearpath else None,
        ),
    )


class SimulationService:
    def __init__(
        self,
        event_service: EventService,
        *,
        adapter_factory: AdapterFactory | None = None,
        settings: Settings | None = None,
        scenario_builder: ScenarioBuilder | None = None,
        runner: SimulationRunner | None = None,
    ) -> None:
        self.event_service = event_service
        self.settings = settings or get_settings()
        self.adapter_factory = adapter_factory or default_adapter_factory(self.settings)
        self.scenario_builder = scenario_builder or ScenarioBuilder(self.settings)
        self.runner = runner or SimulationRunner(self.settings)

    async def run_baseline(
        self, db: AsyncSession, mission_id: UUID, request: SimulationRequest
    ) -> SimulationRunRead:
        scenario = await self.scenario_builder.build(
            db, mission_id, SimulationMode.BASELINE, request
        )
        await db.commit()
        return await self._execute_scenario(db, scenario)

    async def run_clearpath(
        self, db: AsyncSession, mission_id: UUID, request: SimulationRequest
    ) -> SimulationRunRead:
        scenario = await self.scenario_builder.build(
            db, mission_id, SimulationMode.CLEARPATH, request
        )
        await db.commit()
        return await self._execute_scenario(db, scenario)

    async def compare(
        self, db: AsyncSession, mission_id: UUID, request: SimulationRequest
    ) -> SimulationComparisonRead:
        """Run both modes over an identical scenario, varying only the mode.

        Network, route, edge mapping, traffic demand, seed, and horizon are shared
        so the only difference between the two runs is CLEARPATH intervention.
        """
        baseline_scenario = await self.scenario_builder.build(
            db, mission_id, SimulationMode.BASELINE, request
        )
        await db.commit()
        clearpath_scenario = baseline_scenario.model_copy(
            update={"mode": SimulationMode.CLEARPATH}
        )
        baseline = await self._execute_scenario(db, baseline_scenario)
        clearpath = await self._execute_scenario(db, clearpath_scenario)
        return SimulationComparisonRead(
            mission_id=mission_id,
            seed=request.seed,
            baseline=baseline,
            clearpath=clearpath,
            comparison=build_comparison_metrics(baseline.metrics, clearpath.metrics),
            correlation_id=baseline_scenario.correlation_id,
        )

    async def get(
        self, db: AsyncSession, mission_id: UUID, simulation_id: UUID
    ) -> SimulationRunRead:
        await self._require_mission(db, mission_id)
        run = await db.scalar(
            select(SimulationRun).where(
                SimulationRun.id == simulation_id,
                SimulationRun.mission_id == mission_id,
            )
        )
        if run is None:
            raise HTTPException(status_code=404, detail="Simulation not found")
        return simulation_run_read(run)

    async def history(
        self,
        db: AsyncSession,
        mission_id: UUID,
        *,
        mode: SimulationMode | None,
        status: SimulationStatus | None,
        limit: int,
    ) -> SimulationHistory:
        await self._require_mission(db, mission_id)
        limit = min(max(limit, 1), MAX_SIMULATION_HISTORY)
        statement = select(SimulationRun).where(SimulationRun.mission_id == mission_id)
        if mode is not None:
            statement = statement.where(SimulationRun.configuration["mode"].astext == mode.value)
        if status is not None:
            statement = statement.where(SimulationRun.status == status)
        statement = statement.order_by(
            SimulationRun.created_at.desc(), SimulationRun.id.desc()
        ).limit(limit + 1)
        rows = list((await db.scalars(statement)).all())
        return SimulationHistory(
            items=[simulation_run_read(row) for row in rows[:limit]],
            limit=limit,
            has_more=len(rows) > limit,
        )

    async def _execute_scenario(
        self, db: AsyncSession, scenario: SimulationScenario
    ) -> SimulationRunRead:
        adapter: SimulationAdapter | None = None
        adapter_error: Exception | None = None
        runner_invoked = False
        try:
            adapter = self.adapter_factory(scenario)
            simulator_name = adapter.name
        except Exception as error:
            adapter_error = error
            simulator_name = "unavailable_simulation_adapter"
        run = SimulationRun(
            mission_id=scenario.mission_id,
            scenario_name=f"{scenario.mode.value}:{scenario.network_id}"[:160],
            scenario_seed=scenario.seed,
            simulator=simulator_name,
            status=SimulationStatus.PENDING,
            started_at=None,
            finished_at=None,
            configuration={
                "mode": scenario.mode.value,
                "network_id": scenario.network_id,
                "scenario": scenario.model_dump(mode="json"),
                "clearpath_actions": [],
                "adapter_metadata": adapter_metadata(adapter),
                "correlation_id": str(scenario.correlation_id),
                "simulation_only": True,
            },
        )
        async with db.begin():
            db.add(run)
            await db.flush()
            await db.refresh(run)
            run.status = SimulationStatus.RUNNING
            run.started_at = datetime.now(timezone.utc)
            start_event = await self.event_service.persist(
                db,
                scenario.mission_id,
                self._event(
                    EventType.SIMULATION_STARTED,
                    scenario,
                    run.id,
                    {"mode": scenario.mode.value, "seed": scenario.seed},
                ),
            )
            requested_event = None
            if scenario.mode is SimulationMode.CLEARPATH:
                requested_event = await self.event_service.persist(
                    db,
                    scenario.mission_id,
                    self._event(
                        EventType.CLEARPATH_REQUESTED,
                        scenario,
                        run.id,
                        {"route_id": str(scenario.route_id)},
                    ),
                )

        # The run is durably RUNNING before any simulation work begins, so a
        # crash can never leave a row stuck in a non-terminal state.
        await self._publish(start_event)
        if requested_event is not None:
            await self._publish(requested_event)

        try:
            if adapter_error is not None:
                raise adapter_error
            if adapter is None:
                raise RuntimeError("Simulation adapter could not be created")
            runner_invoked = True
            execution: SimulationExecution = await asyncio.to_thread(
                self.runner.execute, scenario, adapter
            )
        except Exception as error:
            if adapter is not None and not runner_invoked:
                try:
                    adapter.close()
                except Exception:
                    logger.exception("Failed to close simulation adapter before runner start")
            return await self._mark_failed(
                db,
                run,
                scenario,
                error,
                adapter_metadata=adapter_metadata(adapter),
            )

        action_data = [item.model_dump(mode="json") for item in execution.actions]
        run.configuration = {
            **run.configuration,
            "clearpath_actions": action_data,
            "adapter_metadata": adapter_metadata(adapter),
        }
        run.metrics = execution.metrics.model_dump(mode="json", exclude_none=False)
        run.status = SimulationStatus.COMPLETED
        run.finished_at = datetime.now(timezone.utc)
        await db.commit()
        async with db.begin():
            completed_event = await self.event_service.persist(
                db,
                scenario.mission_id,
                self._event(
                    EventType.SIMULATION_COMPLETED,
                    scenario,
                    run.id,
                    {
                        "mode": scenario.mode.value,
                        "route_completed": execution.metrics.route_completed,
                        "action_count": len(execution.actions),
                    },
                ),
            )
            clearpath_event = None
            if scenario.mode is SimulationMode.CLEARPATH:
                clearpath_event = await self.event_service.persist(
                    db,
                    scenario.mission_id,
                    self._event(
                        EventType.CLEARPATH_UPDATED,
                        scenario,
                        run.id,
                        {"action_count": len(execution.actions), "actions": action_data},
                    ),
                )
            await db.refresh(run)
        await self._publish(completed_event)
        if clearpath_event is not None:
            await self._publish(clearpath_event)
        return simulation_run_read(run)

    async def _mark_failed(
        self,
        db: AsyncSession,
        run: SimulationRun,
        scenario: SimulationScenario,
        error: Exception,
        *,
        adapter_metadata: dict[str, Any] | None = None,
    ) -> SimulationRunRead:
        code = getattr(error, "code", "SIMULATION_FAILURE")
        safe_message = (
            str(error)[:400] or "Simulation execution failed"
            if isinstance(error, SimulationAdapterError)
            else "Simulation execution failed; consult server logs"
        )
        run.status = SimulationStatus.FAILED
        run.finished_at = datetime.now(timezone.utc)
        run.configuration = {
            **run.configuration,
            "error_code": code,
            "error_message": safe_message,
            "adapter_metadata": adapter_metadata or {},
        }
        await db.commit()
        async with db.begin():
            failed_event = await self.event_service.persist(
                db,
                scenario.mission_id,
                self._event(
                    EventType.SIMULATION_FAILED,
                    scenario,
                    run.id,
                    {"error_code": code, "message": safe_message},
                ),
            )
            await db.refresh(run)
        await self._publish(failed_event)
        return simulation_run_read(run)

    async def _publish(self, event: Any) -> None:
        """Publish a persisted event without changing the simulation outcome.

        A transport failure must be visible to the caller, but it must not turn a
        measured COMPLETED or FAILED simulation into a different recorded result.
        """
        try:
            await self.event_service.publish_persisted(event)
        except HTTPException:
            logger.warning("Simulation event %s was persisted but not published", event.id)

    def _event(
        self,
        event_type: EventType,
        scenario: SimulationScenario,
        run_id: UUID,
        payload: dict[str, object],
    ) -> EventCreate:
        return EventCreate(
            event_type=event_type,
            timestamp=datetime.now(timezone.utc),
            source="sentinel_simulation_service",
            correlation_id=scenario.correlation_id,
            payload={
                "simulation_run_id": str(run_id),
                "mode": scenario.mode.value,
                **payload,
            },
        )

    async def _require_mission(self, db: AsyncSession, mission_id: UUID) -> None:
        if await db.scalar(select(Mission.id).where(Mission.id == mission_id)) is None:
            raise HTTPException(status_code=404, detail="Mission not found")

    @staticmethod
    def _delta(baseline: float | None, clearpath: float | None) -> float | None:
        return _delta(baseline, clearpath)

    @staticmethod
    def _int_delta(baseline: int | None, clearpath: int | None) -> int | None:
        return _int_delta(baseline, clearpath)

    @staticmethod
    def _improvement_percent(
        baseline_seconds: float | None, clearpath_seconds: float | None
    ) -> float | None:
        return _improvement_percent(baseline_seconds, clearpath_seconds)


def _delta(baseline: float | None, clearpath: float | None) -> float | None:
    if baseline is None or clearpath is None:
        return None
    return clearpath - baseline


def _int_delta(baseline: int | None, clearpath: int | None) -> int | None:
    if baseline is None or clearpath is None:
        return None
    return clearpath - baseline


def _improvement_percent(
    baseline_seconds: float | None, clearpath_seconds: float | None
) -> float | None:
    """Percentage travel-time reduction, or ``None`` when it cannot be computed."""
    if baseline_seconds is None or baseline_seconds <= 0 or clearpath_seconds is None:
        return None
    return (baseline_seconds - clearpath_seconds) / baseline_seconds * 100