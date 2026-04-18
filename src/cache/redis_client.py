from __future__ import annotations

from redis.asyncio import Redis, from_url

_REDIS_SINGLETON: Redis | None = None


def get_redis(url: str) -> Redis:
    global _REDIS_SINGLETON
    if _REDIS_SINGLETON is None:
        _REDIS_SINGLETON = from_url(url, decode_responses=True)
    return _REDIS_SINGLETON
