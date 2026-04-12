"""/start and the catch-all /unknown handlers."""

from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import ContextTypes

from ..deps import BotDeps
from ..utils import dto_from_telegram_user

logger = logging.getLogger(__name__)


class CommandHandlers:
    def __init__(self, deps: BotDeps) -> None:
        self._deps = deps

    # ── /start ────────────────────────────────────────────────────────────────
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        tg_user = update.effective_user
        if tg_user is None or update.effective_chat is None:
            return

        # Persist / refresh the user row so we remember their language_code.
        user = await self._deps.db.users.upsert(dto_from_telegram_user(tg_user))

        locale = self._deps.translator.pick_locale(user.language_code)
        t = self._deps.translator.gettext

        greeting = t("start-greeting", locale=locale, name=user.first_name or "friend")
        help_text = t("start-help", locale=locale)

        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"{greeting}\n\n{help_text}",
        )

    # ── Catch-all /unknown ────────────────────────────────────────────────────
    async def unknown(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        tg_user = update.effective_user
        if tg_user is None or update.effective_chat is None:
            return
        locale = await self._resolve_locale(tg_user.id, tg_user.language_code)
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=self._deps.translator.gettext("error-unknown-command", locale=locale),
        )

    # ── Helpers ───────────────────────────────────────────────────────────────
    async def _resolve_locale(self, telegram_user_id: int, fallback_lang: str | None) -> str:
        row = await self._deps.db.users.get_by_telegram_id(telegram_user_id)
        lang = row.language_code if row else fallback_lang
        return self._deps.translator.pick_locale(lang)
