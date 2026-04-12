"""Async Redis wrapper for short-lived / rate-limit data."""

from __future__ import annotations

from typing import Any

from redis.asyncio import Redis, from_url

_REDIS_SINGLETON: Redis | None = None


def get_redis(url: str) -> Redis:
    """Process-wide async Redis client."""
    global _REDIS_SINGLETON
    if _REDIS_SINGLETON is None:
        _REDIS_SINGLETON = from_url(url, decode_responses=True)
    return _REDIS_SINGLETON


class RedisCache:
    """High-level helpers for bot features (save/read/flags)."""

    # Keyspace prefix for /redis_save values.
    SAVE_PREFIX = "user:save:"
    # Keyspace prefix for "waiting for next message" flag.
    WAIT_SAVE_PREFIX = "user:wait_save:"

    def __init__(self, client: Redis, default_ttl: int = 3600) -> None:
        self._client = client
        self._default_ttl = default_ttl

    # ── /redis_save ───────────────────────────────────────────────────────────
    async def mark_waiting_for_save(self, user_id: int, ttl: int = 300) -> None:
        await self._client.set(f"{self.WAIT_SAVE_PREFIX}{user_id}", "1", ex=ttl)

    async def is_waiting_for_save(self, user_id: int) -> bool:
        return bool(await self._client.exists(f"{self.WAIT_SAVE_PREFIX}{user_id}"))

    async def clear_waiting_for_save(self, user_id: int) -> None:
        await self._client.delete(f"{self.WAIT_SAVE_PREFIX}{user_id}")

    async def save_text(self, user_id: int, text: str) -> None:
        kwargs: dict[str, Any] = {}
        if self._default_ttl > 0:
            kwargs["ex"] = self._default_ttl
        await self._client.set(f"{self.SAVE_PREFIX}{user_id}", text, **kwargs)

    async def read_text(self, user_id: int) -> str | None:
        return await self._client.get(f"{self.SAVE_PREFIX}{user_id}")

    # ── Direct access (rare) ──────────────────────────────────────────────────
    @property
    def client(self) -> Redis:
        return self._client
