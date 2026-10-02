"""Mission-scoped simulation execution and retrieval APIs."""

from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.enums import SimulationStatus
from app.schemas.simulation import (
    SimulationComparisonRead,
    SimulationHistory,
    SimulationMode,
    SimulationRequest,
    SimulationRunRead,
)
from app.services.event_bus import EventPublisher
from app.services.event_service import EventService
from app.services.simulation.service import AdapterFactory, SimulationService

router = APIRouter(prefix="/missions", tags=["simulations"])


def _simulation_service(request: Request) -> SimulationService:
    """Build the service for one request.

    ``app.state.simulation_adapter_factory`` is an optional dependency-injection
    seam: when set it replaces the default adapter selection so a deployment or
    a test can supply another ``SimulationAdapter`` implementation. Routes never
    construct a simulator or speak TraCI themselves.
    """
    event_service = EventService(EventPublisher(request.app.state.redis))
    adapter_factory: AdapterFactory | None = getattr(
        request.app.state, "simulation_adapter_factory", None
    )
    return SimulationService(event_service, adapter_factory=adapter_factory)


@router.post(
    "/{mission_id}/simulations/baseline",
    response_model=SimulationRunRead,
    status_code=status.HTTP_201_CREATED,
    summary="Run a baseline digital-twin simulation",
    description=(
        "Runs normal simulated traffic without CLEARPATH intervention. "
        "The API does not control real traffic infrastructure."
    ),
)
async def run_baseline_simulation(
    mission_id: UUID,
    payload: SimulationRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> SimulationRunRead:
    return await _simulation_service(request).run_baseline(db, mission_id, payload)


@router.post(
    "/{mission_id}/simulations/clearpath",
    response_model=SimulationRunRead,
    status_code=status.HTTP_201_CREATED,
    summary="Run a CLEARPATH digital-twin simulation",
    description=(
        "CLEARPATH actions are safety-guarded and affect only the configured "
        "SUMO digital twin. They do not control real-world signals."
    ),
)
async def run_clearpath_simulation(
    mission_id: UUID,
    payload: SimulationRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> SimulationRunRead:
    return await _simulation_service(request).run_clearpath(db, mission_id, payload)


@router.post(
    "/{mission_id}/simulations/compare",
    response_model=SimulationComparisonRead,
    status_code=status.HTTP_201_CREATED,
    summary="Compare baseline and CLEARPATH simulations",
    description=(
        "Runs both modes with the same network, route mapping, traffic configuration, "
        "and deterministic seed. CLEARPATH may improve, have no effect, or perform worse."
    ),
)
async def compare_simulations(
    mission_id: UUID,
    payload: SimulationRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> SimulationComparisonRead:
    return await _simulation_service(request).compare(db, mission_id, payload)


@router.get(
    "/{mission_id}/simulations",
    response_model=SimulationHistory,
    summary="List mission simulation runs",
)
async def list_simulations(
    mission_id: UUID,
    request: Request,
    mode: SimulationMode | None = None,
    run_status: SimulationStatus | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
) -> SimulationHistory:
    return await _simulation_service(request).history(
        db,
        mission_id,
        mode=mode,
        status=run_status,
        limit=limit,
    )


@router.get(
    "/{mission_id}/simulations/{simulation_id}",
    response_model=SimulationRunRead,
    summary="Get a mission simulation run",
)
async def get_simulation(
    mission_id: UUID,
    simulation_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> SimulationRunRead:
    return await _simulation_service(request).get(db, mission_id, simulation_id)