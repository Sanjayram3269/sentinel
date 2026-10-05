"""FastAPI application entry point for SENTINEL."""

from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import router as api_v1_router
from app.config import get_settings
from app.db.session import engine
from app.services.redis import create_redis_client
from app.services.websocket_manager import WebSocketManager
from app.api.v1.websockets import router as websocket_router


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    """Manage service clients for the lifetime of the application."""
    settings = get_settings()
    application.state.redis = create_redis_client(settings.redis_url)
    application.state.websocket_manager = WebSocketManager()
    try:
        yield
    finally:
        await application.state.websocket_manager.shutdown()
        await application.state.redis.aclose()
        await engine.dispose()


settings = get_settings()
app = FastAPI(
    title=settings.app_name,
    description=(
        "Backend foundation for the predictive geoagentic emergency-response "
        "system."
    ),
    version=settings.app_version,
    lifespan=lifespan,
)
app.include_router(api_v1_router)
app.include_router(websocket_router)

# Browser access is restricted to the configured origin allowlist. The
# middleware is added after the routers so it also wraps mounted sub-apps
# in the future; route and schema registration above is unchanged.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "Accept"],
)


@app.get("/", tags=["system"])
async def root() -> dict[str, str]:
    """Identify the SENTINEL service."""
    return {"service": "SENTINEL", "message": "SENTINEL backend is running."}


@app.get("/health", tags=["system"])
async def health() -> dict[str, str]:
    """Report that the application process is responding."""
    return {"status": "ok", "service": "sentinel-backend"}