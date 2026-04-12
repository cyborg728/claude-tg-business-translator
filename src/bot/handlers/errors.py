"""Global error handler — catches any unhandled exception in any handler."""

from __future__ import annotations

import logging
import traceback

from telegram import Update
from telegram.ext import ContextTypes

from src.i18n import get_translator

logger = logging.getLogger(__name__)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log the error and try to send a short notice to the user."""
    logger.error(
        "Exception while handling update: %s\n%s",
        context.error,
        "".join(traceback.format_exception(context.error)) if context.error else "",
    )

    # Best-effort user notification — never raise from the error handler.
    if not isinstance(update, Update) or update.effective_chat is None:
        return
    tg_user = update.effective_user
    locale = get_translator().pick_locale(tg_user.language_code if tg_user else None)
    try:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=get_translator().gettext("error-internal", locale=locale),
        )
    except Exception:  # pragma: no cover
        logger.exception("Failed to send error message to user")
