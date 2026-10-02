"""Mission-isolated WebSocket connections bridged from Redis Pub/Sub."""

import asyncio
import json
from collections import defaultdict
from uuid import UUID

from fastapi import WebSocket, WebSocketDisconnect
from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.services.event_bus import mission_channel


class WebSocketManager:
    def __init__(self) -> None:
        self._connections: dict[UUID, set[WebSocket]] = defaultdict(set)
        self._subscriptions: dict[UUID, tuple[object, asyncio.Task[None]]] = {}
        self._lock = asyncio.Lock()

    async def connect(
        self, mission_id: UUID, websocket: WebSocket, redis: Redis
    ) -> None:
        await websocket.accept()
        async with self._lock:
            if mission_id not in self._subscriptions:
                pubsub = redis.pubsub()
                try:
                    await pubsub.subscribe(mission_channel(mission_id))
                except (RedisError, OSError):
                    await websocket.close(code=1013)
                    await pubsub.aclose()
                    raise
                task = asyncio.create_task(self._listen(mission_id, pubsub))
                self._subscriptions[mission_id] = (pubsub, task)
            self._connections[mission_id].add(websocket)

    async def disconnect(self, mission_id: UUID, websocket: WebSocket) -> None:
        async with self._lock:
            connections = self._connections.get(mission_id)
            if connections is None:
                return
            connections.discard(websocket)
            if connections:
                return
            self._connections.pop(mission_id, None)
            subscription = self._subscriptions.pop(mission_id, None)
        if subscription is not None:
            _, task = subscription
            if task is asyncio.current_task():
                return
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def broadcast(self, mission_id: UUID, message: dict[str, object]) -> None:
        for websocket in tuple(self._connections.get(mission_id, ())):
            try:
                await websocket.send_json(message)
            except (WebSocketDisconnect, RuntimeError):
                await self.disconnect(mission_id, websocket)

    async def shutdown(self) -> None:
        subscriptions = list(self._subscriptions.values())
        self._subscriptions.clear()
        self._connections.clear()
        for _, task in subscriptions:
            task.cancel()
        for _, task in subscriptions:
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def _listen(self, mission_id: UUID, pubsub: object) -> None:
        try:
            async for message in pubsub.listen():
                if message.get("type") != "message":
                    continue
                try:
                    envelope = json.loads(message["data"])
                except (TypeError, json.JSONDecodeError):
                    continue
                if envelope.get("mission_id") == str(mission_id):
                    await self.broadcast(mission_id, envelope)
        except asyncio.CancelledError:
            raise
        except (RedisError, OSError):
            connections = tuple(self._connections.pop(mission_id, ()))
            self._subscriptions.pop(mission_id, None)
            for websocket in connections:
                try:
                    await websocket.close(code=1013)
                except RuntimeError:
                    pass
        finally:
            await pubsub.aclose()