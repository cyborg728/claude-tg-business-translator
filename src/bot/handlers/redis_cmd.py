"""/redis_save and /redis_read — ephemeral text stashing via Redis."""

from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import ContextTypes

from ..deps import BotDeps

logger = logging.getLogger(__name__)


class RedisHandlers:
    def __init__(self, deps: BotDeps) -> None:
        self._deps = deps

    # ── /redis_save ──────────────────────────────────────────────────────────
    async def redis_save(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        tg_user = update.effective_user
        chat = update.effective_chat
        if tg_user is None or chat is None:
            return

        locale = await self._locale_for(tg_user.id, tg_user.language_code)

        await self._deps.cache.mark_waiting_for_save(tg_user.id, ttl=300)
        await context.bot.send_message(
            chat_id=chat.id,
            text=self._deps.translator.gettext("redis-save-prompt", locale=locale),
        )

    # ── /redis_read ──────────────────────────────────────────────────────────
    async def redis_read(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        tg_user = update.effective_user
        chat = update.effective_chat
        if tg_user is None or chat is None:
            return

        locale = await self._locale_for(tg_user.id, tg_user.language_code)
        value = await self._deps.cache.read_text(tg_user.id)

        t = self._deps.translator.gettext
        if value is None:
            text = t("redis-read-empty", locale=locale)
        else:
            text = t("redis-read-value", locale=locale, value=value)
        await context.bot.send_message(chat_id=chat.id, text=text, parse_mode="HTML")

    # ── Plain text capture (runs only when /redis_save was the previous cmd) ─
    async def capture_save(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        tg_user = update.effective_user
        chat = update.effective_chat
        message = update.effective_message
        if tg_user is None or chat is None or message is None or not message.text:
            return

        if not await self._deps.cache.is_waiting_for_save(tg_user.id):
            return  # not in save-mode → let other handlers run

        await self._deps.cache.save_text(tg_user.id, message.text)
        await self._deps.cache.clear_waiting_for_save(tg_user.id)

        locale = await self._locale_for(tg_user.id, tg_user.language_code)
        await context.bot.send_message(
            chat_id=chat.id,
            text=self._deps.translator.gettext("redis-save-done", locale=locale),
        )

    # ── Helpers ──────────────────────────────────────────────────────────────
    async def _locale_for(self, tg_id: int, fallback: str | None) -> str:
        row = await self._deps.db.users.get_by_telegram_id(tg_id)
        return self._deps.translator.pick_locale(row.language_code if row else fallback)
