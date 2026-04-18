from __future__ import annotations

import os

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "0:test")
os.environ.setdefault("RABBITMQ_URL", "memory://")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")

import pytest_asyncio


@pytest_asyncio.fixture
async def fake_redis():
    import fakeredis.aioredis

    client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    try:
        yield client
    finally:
        await client.aclose()
