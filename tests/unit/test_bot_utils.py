"""bot.utils.dto_from_telegram_user — UserDTO conversion."""

from __future__ import annotations

from types import SimpleNamespace

from src.bot.utils import dto_from_telegram_user


def _tg_user(**overrides):
    base = dict(
        id=42,
        username="alice",
        first_name="Alice",
        last_name="Anderson",
        language_code="en-US",
        is_bot=False,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_dto_from_telegram_user_full_profile():
    dto = dto_from_telegram_user(_tg_user())
    assert dto.telegram_user_id == 42
    assert dto.username == "alice"
    assert dto.first_name == "Alice"
    assert dto.last_name == "Anderson"
    assert dto.language_code == "en-US"


def test_dto_from_telegram_user_handles_missing_fields():
    dto = dto_from_telegram_user(
        _tg_user(username=None, last_name=None, language_code=None)
    )
    assert dto.username is None
    assert dto.last_name is None
    assert dto.language_code is None


def test_dto_from_telegram_user_first_name_fallback_to_empty():
    dto = dto_from_telegram_user(_tg_user(first_name=None))
    assert dto.first_name == ""


def test_dto_id_left_blank_for_repository_to_fill():
    """Repositories generate a UUIDv7 server-side; the DTO ``id`` is ignored."""
    dto = dto_from_telegram_user(_tg_user())
    assert dto.id == ""
