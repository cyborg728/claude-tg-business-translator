"""Assemble the PTB :class:`Application` with all handlers wired up.

Non-blocking handlers
---------------------
``ApplicationBuilder().concurrent_updates(True)`` makes PTB dispatch updates in
parallel — long-running tasks (DB, network) won't block other commands.
"""

from __future__ import annotations

import logging

from telegram.ext import (
    AIORateLimiter,
    Application,
    ApplicationBuilder,
    BusinessConnectionHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

from .deps import BotDeps
from .handlers import (
    BusinessHandlers,
    CommandHandlers,
    RedisHandlers,
    SmokeHandlers,
    error_handler,
)

logger = logging.getLogger(__name__)

# PTB runs groups in ascending order; within a single group the FIRST matching
# handler wins. We keep everything in one group so known commands are picked
# up before the catch-all unknown handler (registered last).


def build_application(deps: BotDeps) -> Application:
    """Create a fully-configured PTB Application."""
    commands = CommandHandlers(deps)
    smoke = SmokeHandlers(deps)
    redis_h = RedisHandlers(deps)
    business = BusinessHandlers(deps)

    app: Application = (
        ApplicationBuilder()
        .token(deps.settings.telegram_bot_token)
        # Parallel dispatch → handlers run non-blocking.
        .concurrent_updates(True)
        # Client-side rate limiter (separate from our server-side delivery limit).
        .rate_limiter(AIORateLimiter())
        .build()
    )

    # ── Commands ──────────────────────────────────────────────────────────────
    app.add_handler(CommandHandler("start", commands.start, block=False))
    app.add_handler(CommandHandler("smoke", smoke.smoke, block=False))
    app.add_handler(CommandHandler("redis_save", redis_h.redis_save, block=False))
    app.add_handler(CommandHandler("redis_read", redis_h.redis_read, block=False))

    # ── Business account ──────────────────────────────────────────────────────
    app.add_handler(
        BusinessConnectionHandler(business.handle_connection, block=False)
    )
    app.add_handler(
        MessageHandler(
            filters.UpdateType.BUSINESS_MESSAGE & filters.TEXT & ~filters.COMMAND,
            business.handle_business_message,
            block=False,
        )
    )

    # ── Plain-text capture for /redis_save ───────────────────────────────────
    # Handler no-ops unless the user just issued /redis_save.
    app.add_handler(
        MessageHandler(
            filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND,
            redis_h.capture_save,
            block=False,
        ),
    )

    # ── Unknown command fallback (must be LAST in the group) ──────────────────
    app.add_handler(
        MessageHandler(filters.COMMAND, commands.unknown, block=False),
    )

    # ── Errors ────────────────────────────────────────────────────────────────
    app.add_error_handler(error_handler)

    logger.info("Application built — mode=%s", deps.settings.mode)
    return app
