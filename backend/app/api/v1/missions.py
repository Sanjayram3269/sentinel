"""Mission creation, lookup, and aggregate state endpoints."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models import Mission
from app.schemas.domain import MissionCreate, MissionRead, MissionStateRead
from app.services.mission_state import get_mission_state as build_mission_state

router = APIRouter(prefix="/missions", tags=["missions"])


@router.post("", response_model=MissionRead, status_code=status.HTTP_201_CREATED)
async def create_mission(
    payload: MissionCreate,
    db: AsyncSession = Depends(get_db),
) -> MissionRead:
    async with db.begin():
        mission = Mission(**payload.model_dump())
        db.add(mission)
        await db.flush()
        await db.refresh(mission)
    return MissionRead.model_validate(mission)


@router.get("/{mission_id}", response_model=MissionRead)
async def get_mission(
    mission_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> MissionRead:
    mission = await db.get(Mission, mission_id)
    if mission is None:
        raise HTTPException(status_code=404, detail="Mission not found")
    return MissionRead.model_validate(mission)


@router.get("/{mission_id}/state", response_model=MissionStateRead)
async def get_mission_state(
    mission_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> MissionStateRead:
    return await build_mission_state(db, mission_id)