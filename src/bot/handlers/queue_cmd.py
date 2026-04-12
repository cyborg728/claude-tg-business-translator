"""/test_queue — enqueue a slow job via Celery and reply when done."""

from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import ContextTypes

from src.tasks.processing import test_queue as celery_test_queue

from ..deps import BotDeps

logger = logging.getLogger(__name__)


class QueueHandlers:
    def __init__(self, deps: BotDeps) -> None:
        self._deps = deps

    async def test_queue(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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
            text=self._deps.translator.gettext("queue-enqueued", locale=locale),
        )

        # Enqueue. The worker will hand the result over to the delivery queue.
        celery_test_queue.delay(chat_id=chat.id, locale=locale, delay_s=5)
