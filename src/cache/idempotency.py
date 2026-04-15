"""Per-``update_id`` deduplication for Telegram webhooks / polling.

Telegram retries ``getUpdates`` / webhook deliveries on network hiccups,
and the receiver side may also re-queue updates during rollouts. Without
a first-line guard we'd process the same update twice.

We keep a short-lived ``dedup:update:<id>`` key in Redis with ``SET NX
EX`` semantics — the first caller claims the id, everyone else sees a
"duplicate" answer and short-circuits.

The window (``ttl``) is deliberately larger than any realistic Telegram
retry interval. One hour is a safe default; callers can tune via
``settings.dedup_ttl_seconds``.
"""

from __future__ import annotations

from redis.asyncio import Redis

from src.tasks.metrics import dedup_hit_total, dedup_miss_total

_KEY_PREFIX = "dedup:update:"


def _key(update_id: int | str) -> str:
    return f"{_KEY_PREFIX}{update_id}"


async def claim_update(
    client: Redis,
    update_id: int | str,
    *,
    ttl_seconds: int,
) -> bool:
    """Atomically claim an ``update_id``. Returns ``True`` if this call was
    the first (caller should process the update); ``False`` if another
    caller already claimed it (duplicate — caller should drop).

    Uses ``SET key "1" NX EX ttl`` so the claim is atomic and self-expiring.
    """
    # ``set(..., nx=True)`` returns True on success, None when the key
    # already exists — we normalize to a bool.
    created = await client.set(_key(update_id), "1", nx=True, ex=ttl_seconds)
    if created:
        dedup_miss_total.inc()
        return True
    dedup_hit_total.inc()
    return False


async def has_seen(client: Redis, update_id: int | str) -> bool:
    """Non-destructive check — useful for tests / introspection."""
    return bool(await client.exists(_key(update_id)))
