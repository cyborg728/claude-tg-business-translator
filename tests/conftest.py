"""Shared fixtures.

Most tests use:
* ``sqlite_db``       — fresh in-memory ``SqliteDatabase`` (one per test).
* ``redis_cache``     — fakeredis-backed ``RedisCache``.
* ``translator``      — process-wide ``Translator`` (default locale ``en``).
"""

from __future__ import annotations

import os

# Make sure the bot's pydantic Settings can be instantiated even when the
# repo doesn't ship a real .env (pure-CI run). Set BEFORE any src.* import.
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "0:test")
os.environ.setdefault("MODE", "polling")
os.environ.setdefault("DEFAULT_LOCALE", "en")
os.environ.setdefault("DATABASE_BACKEND", "sqlite")
os.environ.setdefault("DATABASE_PATH", ":memory:")
os.environ.setdefault("RABBITMQ_URL", "memory://")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")

from typing import AsyncIterator

import pytest
import pytest_asyncio

from src.cache import RedisCache
from src.databases.sqlite import SqliteDatabase
from src.i18n import Translator


@pytest_asyncio.fixture
async def sqlite_db() -> AsyncIterator[SqliteDatabase]:
    """In-memory SQLite database with all tables created via metadata."""
    db = SqliteDatabase("sqlite+aiosqlite:///:memory:")
    await db.connect()

    # Skip Alembic for unit-level repo tests — create tables straight from
    # SQLAlchemy metadata. Alembic itself is exercised in test_alembic_migrations.
    from src.databases.sqlite.models import Base

    async with db._engine.begin() as conn:        # type: ignore[union-attr]
        await conn.run_sync(Base.metadata.create_all)

    try:
        yield db
    finally:
        await db.disconnect()


@pytest_asyncio.fixture
async def fake_redis():
    """Async fakeredis client — drop-in replacement for redis.asyncio.Redis."""
    import fakeredis.aioredis

    client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    try:
        yield client
    finally:
        await client.aclose()


@pytest_asyncio.fixture
async def redis_cache(fake_redis) -> RedisCache:
    return RedisCache(fake_redis, default_ttl=60)


@pytest.fixture(scope="session")
def translator() -> Translator:
    return Translator(default_locale="en")
