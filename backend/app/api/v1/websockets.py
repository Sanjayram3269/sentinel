"""Mission-specific real-time event streams."""

from uuid import UUID

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from redis.exceptions import RedisError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models import Mission
from app.services.websocket_manager import WebSocketManager

router = APIRouter(tags=["mission streams"])


@router.websocket("/ws/missions/{mission_id}")
async def mission_event_stream(
    websocket: WebSocket,
    mission_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> None:
    mission = await db.get(Mission, mission_id)
    if mission is None:
        await websocket.close(code=4404, reason="Mission not found")
        return

    manager: WebSocketManager = websocket.app.state.websocket_manager
    try:
        await manager.connect(mission_id, websocket, websocket.app.state.redis)
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    except (RedisError, OSError):
        if websocket.client_state.name == "CONNECTED":
            await websocket.close(code=1013, reason="Event stream unavailable")
    finally:
        await manager.disconnect(mission_id, websocket)