"""Polling / webhook bootstrap for the bot."""

from __future__ import annotations

import asyncio
import logging

from src.cache import RedisCache, get_redis
from src.config import Settings, get_settings
from src.databases import create_database
from src.i18n import get_translator

from .application import build_application
from .deps import BotDeps

logger = logging.getLogger(__name__)

_ALLOWED_UPDATES = [
    "message",
    "edited_message",
    "callback_query",
    "business_connection",
    "business_message",
    "edited_business_message",
]


async def run_bot(settings: Settings | None = None) -> None:
    settings = settings or get_settings()

    # ── Translator (auto-detects available locales) ──────────────────────────
    translator = get_translator(settings.default_locale)
    logger.info(
        "i18n ready (default=%s, available=%s)",
        translator.default_locale,
        translator.available_locales,
    )

    # ── Database ──────────────────────────────────────────────────────────────
    db = create_database(settings)
    await db.connect()
    logger.info("Database backend=%s connected", settings.database_backend)

    # ── Redis cache ───────────────────────────────────────────────────────────
    redis_client = get_redis(settings.redis_url)
    cache = RedisCache(redis_client, default_ttl=settings.redis_save_ttl)

    deps = BotDeps(settings=settings, db=db, cache=cache, translator=translator)
    app = build_application(deps)

    try:
        if settings.mode == "webhook":
            await _run_webhook(app, settings)
        else:
            await _run_polling(app)
    finally:
        await db.disconnect()
        await redis_client.close()


async def _run_polling(app) -> None:
    logger.info("Starting in POLLING mode")
    async with app:
        await app.start()
        await app.updater.start_polling(
            allowed_updates=_ALLOWED_UPDATES,
            drop_pending_updates=True,
        )
        try:
            await asyncio.Event().wait()
        except (KeyboardInterrupt, asyncio.CancelledError):
            logger.info("Shutdown signal received")
        finally:
            if app.updater and app.updater.running:
                await app.updater.stop()
            if app.running:
                await app.stop()


async def _run_webhook(app, settings: Settings) -> None:
    logger.info(
        "Starting in WEBHOOK mode — listening on 0.0.0.0:%s, public URL %s",
        settings.webhook_port,
        settings.webhook_full_url,
    )
    async with app:
        await app.start()
        await app.updater.start_webhook(
            listen="0.0.0.0",
            port=settings.webhook_port,
            url_path=settings.webhook_path,
            secret_token=settings.webhook_secret_token or None,
            webhook_url=settings.webhook_full_url,
            allowed_updates=_ALLOWED_UPDATES,
            bootstrap_retries=-1,
        )
        try:
            await asyncio.Event().wait()
        except (KeyboardInterrupt, asyncio.CancelledError):
            logger.info("Shutdown signal received")
        finally:
            if app.updater and app.updater.running:
                await app.updater.stop()
            if app.running:
                await app.stop()
