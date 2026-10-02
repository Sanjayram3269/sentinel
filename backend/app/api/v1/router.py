"""Version 1 domain API router."""

from fastapi import APIRouter

from app.api.v1.incidents import router as incidents_router
from app.api.v1.missions import router as missions_router
from app.api.v1.vehicles import router as vehicles_router

router = APIRouter(prefix="/api/v1")
router.include_router(missions_router)
router.include_router(incidents_router)
router.include_router(vehicles_router)