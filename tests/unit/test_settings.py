"""Settings validators and derived URLs."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.config.settings import Settings


def _build(**overrides) -> Settings:
    """Build Settings with required token already set."""
    base = {"telegram_bot_token": "12345:ABC"}
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def test_default_mode_is_polling():
    s = _build()
    assert s.mode == "polling"


def test_mode_is_lowercased():
    s = _build(mode="RECEIVER", webhook_base_url="https://example.f8f.dev")
    assert s.mode == "receiver"


def test_webhook_base_url_strips_trailing_slash():
    s = _build(webhook_base_url="https://example.f8f.dev/")
    assert s.webhook_base_url == "https://example.f8f.dev"


def test_webhook_full_url_concatenates_path():
    s = _build(
        mode="receiver",
        webhook_base_url="https://example.f8f.dev",
        telegram_bot_token="12345:ABC",
    )
    assert s.webhook_full_url == "https://example.f8f.dev/12345:ABC"


def test_receiver_mode_requires_url():
    with pytest.raises(ValidationError):
        _build(mode="receiver", webhook_base_url="")


def test_webhook_mode_rejected():
    with pytest.raises(ValidationError):
        _build(mode="webhook", webhook_base_url="https://example.f8f.dev")


def test_database_url_for_sqlite_uses_async_driver():
    s = _build(database_path="data/test.db")
    assert s.database_url == "sqlite+aiosqlite:///data/test.db"


def test_database_url_sync_used_by_alembic():
    s = _build(database_path="data/test.db")
    assert s.database_url_sync == "sqlite:///data/test.db"


def test_celery_uris_route_to_rabbitmq_and_redis():
    s = _build(
        rabbitmq_url="amqp://x:y@h:5672//",
        redis_url="redis://r:6379/3",
    )
    assert s.celery_broker_url == "amqp://x:y@h:5672//"
    assert s.celery_result_backend == "redis://r:6379/3"
