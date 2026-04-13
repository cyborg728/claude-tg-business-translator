"""RedisCache — wait-for-save flag, save_text, read_text, TTL semantics."""

from __future__ import annotations

import pytest

from src.cache import RedisCache


async def test_initial_state_no_value(redis_cache: RedisCache):
    assert await redis_cache.read_text(1) is None
    assert await redis_cache.is_waiting_for_save(1) is False


async def test_mark_and_clear_waiting_for_save(redis_cache: RedisCache):
    await redis_cache.mark_waiting_for_save(1, ttl=60)
    assert await redis_cache.is_waiting_for_save(1) is True

    await redis_cache.clear_waiting_for_save(1)
    assert await redis_cache.is_waiting_for_save(1) is False


async def test_save_text_then_read(redis_cache: RedisCache):
    await redis_cache.save_text(1, "hello world")
    assert await redis_cache.read_text(1) == "hello world"


async def test_save_text_per_user_isolation(redis_cache: RedisCache):
    await redis_cache.save_text(1, "alpha")
    await redis_cache.save_text(2, "beta")
    assert await redis_cache.read_text(1) == "alpha"
    assert await redis_cache.read_text(2) == "beta"


async def test_save_text_overwrite(redis_cache: RedisCache):
    await redis_cache.save_text(1, "first")
    await redis_cache.save_text(1, "second")
    assert await redis_cache.read_text(1) == "second"


async def test_default_ttl_zero_means_no_expiry(fake_redis):
    cache = RedisCache(fake_redis, default_ttl=0)
    await cache.save_text(1, "forever")
    ttl = await fake_redis.ttl(f"{RedisCache.SAVE_PREFIX}1")
    assert ttl == -1  # redis "no expiry" sentinel


async def test_default_ttl_positive_sets_expiry(fake_redis):
    cache = RedisCache(fake_redis, default_ttl=300)
    await cache.save_text(1, "later")
    ttl = await fake_redis.ttl(f"{RedisCache.SAVE_PREFIX}1")
    assert 0 < ttl <= 300


async def test_wait_flag_uses_short_ttl(fake_redis):
    cache = RedisCache(fake_redis)
    await cache.mark_waiting_for_save(7, ttl=120)
    ttl = await fake_redis.ttl(f"{RedisCache.WAIT_SAVE_PREFIX}7")
    assert 0 < ttl <= 120
