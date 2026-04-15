"""First-line dedup for incoming Telegram updates.

Registered as a ``TypeHandler(Update, …)`` in PTB group ``-1`` so it runs
before any business handler. When the dedup layer detects a duplicate
``update_id``, we raise :class:`telegram.ext.ApplicationHandlerStop` —
PTB then skips every subsequent handler in every later group for this
update, and the bot behaves as if the retransmission never happened.

Why this layer at all: Telegram retries webhook deliveries on transport
errors, and polling can re-fetch an offset on restart. Without this
guard the bot would re-execute handlers (double-writes to DB, double
sends to users).
"""

from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import ApplicationHandlerStop, ContextTypes

from src.cache import claim_update
from src.cache.redis_client import get_redis
from src.config import get_settings

logger = logging.getLogger(__name__)


async def dedup_filter(update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
    """Drop the update if its ``update_id`` was already processed.

    ``claim_update`` returns ``True`` the first time it sees an id (we let
    the update through) and ``False`` on every subsequent call within the
    TTL window (we stop the PTB pipeline).
    """
    if update.update_id is None:  # pragma: no cover — defensive
        return

    settings = get_settings()
    redis = get_redis(settings.redis_url)
    first_time = await claim_update(
        redis, update.update_id, ttl_seconds=settings.dedup_ttl_seconds
    )
    if not first_time:
        logger.info("Duplicate update %s — dropped by dedup", update.update_id)
        raise ApplicationHandlerStop
