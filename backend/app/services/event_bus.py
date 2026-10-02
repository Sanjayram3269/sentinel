"""Redis Pub/Sub transport for durable mission events."""

import json
from uuid import UUID

from redis.asyncio import Redis

from app.models import Event
from app.schemas.events import EventRead, MissionEventEnvelope


def mission_channel(mission_id: UUID) -> str:
    return f"sentinel:mission:{mission_id}:events"


class EventPublisher:
    """Publish serialized event envelopes after the event store commits."""

    def __init__(self, redis: Redis) -> None:
        self.redis = redis

    async def publish(self, event: Event) -> None:
        envelope = MissionEventEnvelope(
            mission_id=event.mission_id,
            event=EventRead.model_validate(event),
        )
        await self.redis.publish(
            mission_channel(event.mission_id),
            json.dumps(envelope.model_dump(mode="json"), separators=(",", ":")),
        )