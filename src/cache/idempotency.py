from __future__ import annotations

from redis.asyncio import Redis

from src.metrics import dedup_hit_total, dedup_miss_total

_KEY_PREFIX = "dedup:update:"


def _key(update_id: int | str) -> str:
    return f"{_KEY_PREFIX}{update_id}"


async def claim_update(
    client: Redis,
    update_id: int | str,
    *,
    ttl_seconds: int,
) -> bool:
    created = await client.set(_key(update_id), "1", nx=True, ex=ttl_seconds)
    if created:
        dedup_miss_total.inc()
        return True
    dedup_hit_total.inc()
    return False


async def has_seen(client: Redis, update_id: int | str) -> bool:
    return bool(await client.exists(_key(update_id)))
