"""/smoke — enqueue a slow job via Celery and reply when done.

End-to-end sanity check for the producer → tasks_queue → delivery_queue
pipeline. Deleted / repurposed when the first real feature lands.
"""

from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import ContextTypes

from src.tasks.processing import smoke as celery_smoke

from ..deps import BotDeps

logger = logging.getLogger(__name__)


class SmokeHandlers:
    def __init__(self, deps: BotDeps) -> None:
        self._deps = deps

    async def smoke(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        tg_user = update.effective_user
        chat = update.effective_chat
        if tg_user is None or chat is None:
            return

        row = await self._deps.db.users.get_by_telegram_id(tg_user.id)
        locale = self._deps.translator.pick_locale(
            row.language_code if row else tg_user.language_code
        )

        # Acknowledge immediately — keeps the bot responsive while the worker
        # crunches in the background.
        await context.bot.send_message(
            chat_id=chat.id,
            text=self._deps.translator.gettext("smoke-enqueued", locale=locale),
        )

        # Enqueue. The worker will hand the result over to the delivery queue.
        celery_smoke.delay(chat_id=chat.id, locale=locale, delay_s=5)
