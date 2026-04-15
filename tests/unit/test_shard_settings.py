"""Phase-3 config surface: UPDATES_SHARDS and shard_queue_name()."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.config.settings import Settings


def _build(**overrides) -> Settings:
    base = {"telegram_bot_token": "12345:ABC"}
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def test_default_shard_count_is_16():
    assert _build().updates_shards == 16


def test_shard_count_rejects_zero():
    with pytest.raises(ValidationError):
        _build(updates_shards=0)


def test_shard_count_rejects_too_many():
    # 256 is the upper bound — past it and consumers become unwieldy.
    with pytest.raises(ValidationError):
        _build(updates_shards=257)


def test_shard_queue_name_format():
    s = _build(updates_shards=4)
    assert s.shard_queue_name(0) == "updates.shard.0"
    assert s.shard_queue_name(3) == "updates.shard.3"


def test_shard_queue_name_bounds_check():
    s = _build(updates_shards=4)
    with pytest.raises(ValueError):
        s.shard_queue_name(4)
    with pytest.raises(ValueError):
        s.shard_queue_name(-1)


def test_exchange_defaults_empty_for_phase2_compat():
    # Existing Phase-2 deployments must keep working without touching
    # their ConfigMap.
    assert _build().updates_exchange == ""
