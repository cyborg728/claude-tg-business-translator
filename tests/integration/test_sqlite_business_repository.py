"""SqliteBusinessConnectionRepository — upsert / get / set_enabled / delete."""

from __future__ import annotations

import pytest

from src.databases.interfaces.business_connection_repository import (
    BusinessConnectionDTO,
    IBusinessConnectionRepository,
)


@pytest.fixture
def repo(sqlite_db) -> IBusinessConnectionRepository:
    return sqlite_db.business_connections


def _dto(**overrides) -> BusinessConnectionDTO:
    base = dict(
        connection_id="conn-123",
        owner_telegram_user_id=42,
        is_enabled=True,
    )
    base.update(overrides)
    return BusinessConnectionDTO(**base)


async def test_implements_interface(sqlite_db):
    assert isinstance(sqlite_db.business_connections, IBusinessConnectionRepository)


async def test_upsert_then_get(repo):
    saved = await repo.upsert(_dto())
    found = await repo.get("conn-123")
    assert found is not None
    assert found.id == saved.id
    assert found.is_enabled is True
    assert found.owner_telegram_user_id == 42


async def test_upsert_idempotent_on_same_connection_id(repo):
    first = await repo.upsert(_dto(owner_telegram_user_id=42))
    second = await repo.upsert(_dto(owner_telegram_user_id=99))
    assert first.id == second.id
    assert second.owner_telegram_user_id == 99


async def test_set_enabled_toggles_flag(repo):
    await repo.upsert(_dto(is_enabled=True))
    await repo.set_enabled("conn-123", False)
    found = await repo.get("conn-123")
    assert found is not None
    assert found.is_enabled is False


async def test_delete_removes_record(repo):
    await repo.upsert(_dto())
    await repo.delete("conn-123")
    assert await repo.get("conn-123") is None


async def test_delete_unknown_is_noop(repo):
    # Must not raise.
    await repo.delete("does-not-exist")
