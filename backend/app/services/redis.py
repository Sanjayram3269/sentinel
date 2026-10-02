"""Async Redis client construction."""

from redis.asyncio import Redis


def create_redis_client(redis_url: str) -> Redis:
    """Create a lazy async Redis client for the configured URL."""
    return Redis.from_url(redis_url, decode_responses=True)