"""SqliteKvStoreRepository — set / get / delete + default fallback."""

from __future__ import annotations

import pytest

from src.databases.interfaces.kv_store_repository import IKvStoreRepository


@pytest.fixture
def repo(sqlite_db) -> IKvStoreRepository:
    return sqlite_db.kv


async def test_implements_interface(sqlite_db):
    assert isinstance(sqlite_db.kv, IKvStoreRepository)


async def test_get_returns_default_when_key_missing(repo):
    assert await repo.get(1, "missing") is None
    assert await repo.get(1, "missing", default="fallback") == "fallback"


async def test_set_then_get(repo):
    await repo.set(1, "theme", "dark")
    assert await repo.get(1, "theme") == "dark"


async def test_set_overwrites_existing_value(repo):
    await repo.set(1, "theme", "dark")
    await repo.set(1, "theme", "light")
    assert await repo.get(1, "theme") == "light"


async def test_isolated_by_owner(repo):
    await repo.set(1, "lang", "ru")
    await repo.set(2, "lang", "en")
    assert await repo.get(1, "lang") == "ru"
    assert await repo.get(2, "lang") == "en"


async def test_delete_removes_value(repo):
    await repo.set(1, "tmp", "x")
    await repo.delete(1, "tmp")
    assert await repo.get(1, "tmp") is None


async def test_delete_unknown_is_noop(repo):
    await repo.delete(1, "never-existed")
