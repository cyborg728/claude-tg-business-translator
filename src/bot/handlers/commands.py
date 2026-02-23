import logging

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from src.i18n import Translator

logger = logging.getLogger(__name__)


class CommandHandlers:
    """Handles /start, /help and unknown commands sent to the bot's private chat."""

    def __init__(self, translator: Translator) -> None:
        self._t = translator

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if update.effective_message is None:
            return
        await update.effective_message.reply_text(
            self._t("start_welcome"),
            parse_mode=ParseMode.HTML,
        )

    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if update.effective_message is None:
            return
        await update.effective_message.reply_text(
            self._t("help_text"),
            parse_mode=ParseMode.HTML,
        )

    async def unknown(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if update.effective_message is None:
            return
        await update.effective_message.reply_text(self._t("unknown_command"))
