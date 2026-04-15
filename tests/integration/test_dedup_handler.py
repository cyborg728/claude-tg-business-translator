"""PTB wiring for :func:`src.bot.handlers.dedup.dedup_filter`.

We don't spin up a full Application — we just verify the handler drops
duplicates by raising ``ApplicationHandlerStop`` and lets new updates
through.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from telegram.ext import ApplicationHandlerStop

from src.bot.handlers import dedup as dedup_module


def _fake_update(update_id: int) -> SimpleNamespace:
    # PTB's ``Update`` only has to provide an ``update_id`` attribute for
    # our filter — no need to construct a full ``telegram.Update``.
    return SimpleNamespace(update_id=update_id)


@pytest.fixture
def patched_redis(fake_redis):
    with patch.object(dedup_module, "get_redis", return_value=fake_redis):
        yield fake_redis


async def test_first_time_update_passes_through(patched_redis):
    # No exception raised → PTB continues dispatching.
    await dedup_module.dedup_filter(_fake_update(1), _context=None)


async def test_duplicate_update_is_stopped(patched_redis):
    await dedup_module.dedup_filter(_fake_update(1), _context=None)
    with pytest.raises(ApplicationHandlerStop):
        await dedup_module.dedup_filter(_fake_update(1), _context=None)


async def test_missing_update_id_is_ignored(patched_redis):
    # Defensive branch — nothing to claim, nothing to stop.
    await dedup_module.dedup_filter(_fake_update(None), _context=None)
