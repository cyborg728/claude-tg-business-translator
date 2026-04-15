"""Dedup helper against fakeredis — hit / miss / TTL semantics."""

from __future__ import annotations

import pytest

from src.cache.idempotency import _KEY_PREFIX, claim_update, has_seen
from src.tasks import metrics


@pytest.fixture
def reset_metrics():
    """Snapshot counter values so each test can assert deltas."""
    before_miss = metrics.dedup_miss_total._value.get()
    before_hit = metrics.dedup_hit_total._value.get()
    yield lambda: (
        metrics.dedup_miss_total._value.get() - before_miss,
        metrics.dedup_hit_total._value.get() - before_hit,
    )


async def test_first_call_wins_second_is_duplicate(fake_redis, reset_metrics):
    first = await claim_update(fake_redis, 42, ttl_seconds=60)
    second = await claim_update(fake_redis, 42, ttl_seconds=60)

    assert first is True
    assert second is False
    miss_delta, hit_delta = reset_metrics()
    assert miss_delta == 1
    assert hit_delta == 1


async def test_different_update_ids_do_not_collide(fake_redis):
    assert await claim_update(fake_redis, 1, ttl_seconds=60) is True
    assert await claim_update(fake_redis, 2, ttl_seconds=60) is True


async def test_ttl_is_applied(fake_redis):
    await claim_update(fake_redis, 7, ttl_seconds=120)
    ttl = await fake_redis.ttl(f"{_KEY_PREFIX}7")
    assert 0 < ttl <= 120


async def test_has_seen_non_destructive(fake_redis):
    # has_seen should never create a key itself.
    assert await has_seen(fake_redis, 99) is False
    assert await claim_update(fake_redis, 99, ttl_seconds=60) is True
    assert await has_seen(fake_redis, 99) is True
    # And calling claim_update again still detects the duplicate afterwards.
    assert await claim_update(fake_redis, 99, ttl_seconds=60) is False


async def test_string_update_id_also_supported(fake_redis):
    # update_id arrives as an int from Telegram; the helper accepts str too
    # so callers don't have to coerce at the boundary.
    assert await claim_update(fake_redis, "abc", ttl_seconds=60) is True
    assert await claim_update(fake_redis, "abc", ttl_seconds=60) is False
