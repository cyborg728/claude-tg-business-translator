from __future__ import annotations

import logging
from typing import Any

from telegram import Update
from telegram.ext import Application, ApplicationBuilder, ContextTypes, TypeHandler

logger = logging.getLogger(__name__)

_START_TEXT = (
    "Привет! Я бот-переводчик для Telegram Business.\n\n"
    "Отправьте /help, чтобы увидеть список команд."
)

_HELP_TEXT = (
    "Доступные команды:\n"
    "/start — приветствие\n"
    "/help — эта справка"
)


class Bot:
    def __init__(
        self,
        token: str,
        *,
        api_base_url: str = "https://api.telegram.org",
    ) -> None:
        self._app: Application = (
            ApplicationBuilder()
            .token(token)
            .base_url(f"{api_base_url.rstrip('/')}/bot")
            .updater(None)
            .build()
        )
        self._app.add_handler(TypeHandler(Update, _dispatch))

    async def start(self) -> None:
        await self._app.initialize()
        await self._app.start()

    async def close(self) -> None:
        if self._app.running:
            await self._app.stop()
        await self._app.shutdown()

    async def handle_update(self, update: dict[str, Any]) -> None:
        tg_update = Update.de_json(update, self._app.bot)
        if tg_update is None:
            return
        await self._app.process_update(tg_update)


async def _dispatch(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None or not message.text:
        return

    command = message.text.split(maxsplit=1)[0].split("@", 1)[0].lower()
    if command == "/start":
        await message.reply_text(_START_TEXT)
    elif command == "/help":
        await message.reply_text(_HELP_TEXT)


__all__ = ["Bot"]
