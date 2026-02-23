"""Entry point for the Telegram Business Translator Bot.

Start modes
-----------
polling (default / development)::

    python main.py

webhook (production)::

    MODE=webhook WEBHOOK_URL=https://example.com python main.py

All configuration is read from environment variables or a ``.env`` file.
See ``.env.example`` for the full list of options.
"""

import asyncio
import logging
import sys

from src.bot import build_application
from src.config import get_settings
from src.database.connection import Database

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stdout,
)
# Reduce noise from external libraries.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)


async def main() -> None:
    settings = get_settings()

    logger.info("Starting bot in %s mode (locale=%s)", settings.mode, settings.locale)

    # ── Database ──────────────────────────────────────────────────────────────
    db = Database(settings.database_url)
    await db.connect()
    logger.info("Database ready at %s", settings.database_path)

    # ── Application ───────────────────────────────────────────────────────────
    app = build_application(settings, db.get_session_factory())

    try:
        if settings.mode == "webhook":
            _validate_webhook(settings)
            logger.info(
                "Webhook mode: listening on 0.0.0.0:%s, registering %s",
                settings.webhook_port,
                settings.webhook_full_url,
            )
            await app.bot.set_webhook(
                url=settings.webhook_full_url,
                secret_token=settings.webhook_secret_token or None,
                allowed_updates=["message", "business_connection", "business_message"],
            )
            # run_webhook is a blocking call that runs the aiohttp server.
            async with app:
                await app.start()
                await app.updater.start_webhook(
                    listen="0.0.0.0",
                    port=settings.webhook_port,
                    url_path=settings.webhook_path,
                    secret_token=settings.webhook_secret_token or None,
                    webhook_url=settings.webhook_full_url,
                )
                # Keep running until interrupted.
                await asyncio.Event().wait()
        else:
            logger.info("Polling mode — starting long-poll loop")
            async with app:
                await app.start()
                await app.updater.start_polling(
                    allowed_updates=["message", "business_connection", "business_message"],
                    drop_pending_updates=True,
                )
                await asyncio.Event().wait()
    except (KeyboardInterrupt, asyncio.CancelledError):
        logger.info("Shutdown signal received")
    finally:
        logger.info("Shutting down …")
        if app.running:
            await app.stop()
        await db.disconnect()
        logger.info("Goodbye.")


def _validate_webhook(settings) -> None:
    if not settings.webhook_url:
        raise ValueError(
            "WEBHOOK_URL must be set when MODE=webhook. "
            "Example: WEBHOOK_URL=https://example.com"
        )


if __name__ == "__main__":
    asyncio.run(main())
