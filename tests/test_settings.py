from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.config.settings import Settings


def _build(**overrides) -> Settings:
    base = {"telegram_bot_token": "12345:ABC"}
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def test_webhook_base_url_strips_trailing_slash():
    s = _build(webhook_base_url="https://example.f8f.dev/")
    assert s.webhook_base_url == "https://example.f8f.dev"


def test_webhook_full_url():
    s = _build(
        webhook_base_url="https://example.f8f.dev",
        telegram_bot_token="12345:ABC",
    )
    assert s.webhook_full_url == "https://example.f8f.dev/12345:ABC"


def test_webhook_path():
    s = _build(telegram_bot_token="12345:ABC")
    assert s.webhook_path == "/12345:ABC"


def test_requires_webhook_url():
    with pytest.raises(ValidationError):
        _build(webhook_base_url="")


def test_defaults():
    s = _build()
    assert s.webhook_port == 8080
    assert s.dedup_ttl_seconds == 3600
    assert s.updates_queue == "updates_queue"
    assert s.updates_shards == 16


def test_shard_queue_name():
    s = _build(updates_shards=4)
    assert s.shard_queue_name(0) == "updates.shard.0"
    assert s.shard_queue_name(3) == "updates.shard.3"
    with pytest.raises(ValueError):
        s.shard_queue_name(4)
    with pytest.raises(ValueError):
        s.shard_queue_name(-1)
