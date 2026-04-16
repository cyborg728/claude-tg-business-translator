"""SqliteMessageMappingRepository — add + get_by_notification_id."""

from __future__ import annotations

import uuid

import pytest

from src.databases.interfaces.message_mapping_repository import (
    IMessageMappingRepository,
    MessageMappingDTO,
)


@pytest.fixture
def repo(sqlite_db) -> IMessageMappingRepository:
    return sqlite_db.message_mappings


def _dto(**overrides) -> MessageMappingDTO:
    base = dict(
        business_connection_id="conn-1",
        user_telegram_id=11,
        user_chat_id=22,
        original_message_id=1001,
        notification_message_id=2002,
        original_text="Hi there",
        user_language="ru",
    )
    base.update(overrides)
    return MessageMappingDTO(**base)


async def test_implements_interface(sqlite_db):
    assert isinstance(sqlite_db.message_mappings, IMessageMappingRepository)


async def test_add_assigns_uuid7_and_persists(repo):
    saved = await repo.add(_dto())
    assert isinstance(saved.id, uuid.UUID)
    assert saved.id.version == 7
    assert saved.created_at is not None


async def test_get_by_notification_id_roundtrips(repo):
    saved = await repo.add(_dto(notification_message_id=2002))
    found = await repo.get_by_notification_id(2002)
    assert found is not None
    assert found.id == saved.id
    assert found.original_text == "Hi there"
    assert found.user_language == "ru"


async def test_get_by_notification_id_returns_none_for_unknown(repo):
    assert await repo.get_by_notification_id(123_456_789) is None


async def test_multiple_mappings_isolated_by_notification_id(repo):
    await repo.add(_dto(notification_message_id=1, original_text="first"))
    await repo.add(_dto(notification_message_id=2, original_text="second"))
    a = await repo.get_by_notification_id(1)
    b = await repo.get_by_notification_id(2)
    assert a is not None and a.original_text == "first"
    assert b is not None and b.original_text == "second"
