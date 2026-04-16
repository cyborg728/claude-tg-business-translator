"""SqliteUserRepository — upsert / get / set_language / language preservation."""

from __future__ import annotations

import uuid

import pytest

from src.databases.interfaces.user_repository import IUserRepository, UserDTO


@pytest.fixture
def repo(sqlite_db) -> IUserRepository:
    return sqlite_db.users


def _dto(**overrides) -> UserDTO:
    base = dict(
        telegram_user_id=42,
        username="alice",
        first_name="Alice",
        last_name="Anderson",
        language_code="en",
    )
    base.update(overrides)
    return UserDTO(**base)


async def test_implements_interface(sqlite_db):
    assert isinstance(sqlite_db.users, IUserRepository)


async def test_upsert_creates_row_with_uuid7_pk(repo):
    out = await repo.upsert(_dto())
    assert out.telegram_user_id == 42
    assert isinstance(out.id, uuid.UUID)
    assert out.id.version == 7


async def test_upsert_then_get_roundtrips(repo):
    await repo.upsert(_dto())
    found = await repo.get_by_telegram_id(42)
    assert found is not None
    assert found.username == "alice"
    assert found.first_name == "Alice"
    assert found.language_code == "en"


async def test_get_returns_none_for_unknown_user(repo):
    assert await repo.get_by_telegram_id(99999) is None


async def test_upsert_updates_existing_user(repo):
    first = await repo.upsert(_dto(username="alice", first_name="Alice"))
    second = await repo.upsert(_dto(username="alice2", first_name="Alicia"))
    # Same UUIDv7 — same row.
    assert first.id == second.id
    assert second.username == "alice2"
    assert second.first_name == "Alicia"


async def test_upsert_does_not_overwrite_language_when_dto_value_is_none(repo):
    """language_code=None in the DTO must NOT clear an existing value."""
    await repo.upsert(_dto(language_code="ru"))
    await repo.upsert(_dto(username="alice2", language_code=None))
    found = await repo.get_by_telegram_id(42)
    assert found is not None
    assert found.language_code == "ru"


async def test_set_language_updates_only_language(repo):
    await repo.upsert(_dto(language_code="ru"))
    await repo.set_language(42, "de")
    found = await repo.get_by_telegram_id(42)
    assert found is not None
    assert found.language_code == "de"
    assert found.username == "alice"


async def test_set_language_noop_for_unknown_user(repo):
    # Must not raise.
    await repo.set_language(99999, "de")
