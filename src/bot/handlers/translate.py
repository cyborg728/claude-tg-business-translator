import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from src.database.repositories.authorized_user import IAuthorizedUserRepository
from src.database.repositories.bot_setting import IBotSettingRepository
from src.database.repositories.language import ILanguageRepository
from src.i18n import Translator
from src.services import TranslationService

logger = logging.getLogger(__name__)

# BotSetting key for storing per-user target language preference.
TARGET_LANGUAGE_KEY = "target_language"

# Callback-data prefix for language picker buttons.
_CB_LANG_PREFIX = "tr:lang:"
_CB_CHANGE_LANG = "tr:change_lang"


class TranslateHandlers:
    """Handles the /translate command and its inline-keyboard language picker.

    Flow
    ----
    /translate [text]
      • If no preferred language saved → show language picker
        - On selection: save language, then translate (if text provided) or ask for text
      • If preferred language saved + text provided → translate immediately
      • If preferred language saved + no text → ask for text (show Change language button)

    Text input (AWAITING_TEXT state)
      • Detect source language, translate to saved target, show result
    """

    _AWAITING_TEXT = "tr_awaiting_text"
    _PENDING_TEXT = "tr_pending_text"

    def __init__(
        self,
        translator: Translator,
        translation_service: TranslationService,
        language_repo: ILanguageRepository,
        bot_setting_repo: IBotSettingRepository,
        authorized_user_repo: IAuthorizedUserRepository,
    ) -> None:
        self._t = translator
        self._translation = translation_service
        self._languages = language_repo
        self._settings = bot_setting_repo
        self._auth_users = authorized_user_repo

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _owner_id(self, update: Update) -> int:
        return update.effective_chat.id

    async def _get_target_lang(self, owner_chat_id: int) -> str | None:
        val = await self._settings.get(owner_chat_id, TARGET_LANGUAGE_KEY, "")
        return val or None

    async def _build_lang_keyboard(self) -> InlineKeyboardMarkup:
        languages = await self._languages.list_all()
        buttons = []
        row: list[InlineKeyboardButton] = []
        for lang in languages:
            label = self._t(lang.name_key)
            row.append(InlineKeyboardButton(label, callback_data=f"{_CB_LANG_PREFIX}{lang.code}"))
            if len(row) == 2:
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)
        return InlineKeyboardMarkup(buttons)

    def _ask_text_keyboard(self) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            [[InlineKeyboardButton(self._t("translate_change_lang"), callback_data=_CB_CHANGE_LANG)]]
        )

    async def _do_translate(self, update: Update, text: str, target_lang: str) -> None:
        """Detect source language, translate, and reply with the result."""
        try:
            source_lang = await self._translation.detect_language(text)
            translated = await self._translation.translate(
                text, target_language=target_lang, source_language=source_lang
            )
        except Exception as exc:
            logger.error("Translation failed: %s", exc)
            await update.effective_message.reply_text(
                self._t("translation_error", error=str(exc)),
                parse_mode=ParseMode.HTML,
            )
            return

        source_label = self._t(f"lang_{source_lang}") if source_lang else source_lang or "?"
        target_label = self._t(f"lang_{target_lang}")
        result_text = self._t(
            "translate_result",
            source=source_label,
            target=target_label,
            translation=translated,
        )
        keyboard = InlineKeyboardMarkup(
            [[InlineKeyboardButton(self._t("translate_change_lang"), callback_data=_CB_CHANGE_LANG)]]
        )
        await update.effective_message.reply_text(
            result_text,
            reply_markup=keyboard,
            parse_mode=ParseMode.HTML,
        )

    # ── Command ───────────────────────────────────────────────────────────────

    async def cmd_translate(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Entry point: /translate [text]"""
        owner_id = self._owner_id(update)
        # Text passed inline: /translate Hello world
        inline_text = " ".join(context.args) if context.args else ""
        target_lang = await self._get_target_lang(owner_id)

        if target_lang:
            if inline_text:
                await self._do_translate(update, inline_text, target_lang)
            else:
                context.user_data[self._AWAITING_TEXT] = True
                await update.message.reply_text(
                    self._t("translate_ask_text"),
                    reply_markup=self._ask_text_keyboard(),
                )
        else:
            # No language set yet — show picker.
            if inline_text:
                context.user_data[self._PENDING_TEXT] = inline_text
            keyboard = await self._build_lang_keyboard()
            await update.message.reply_text(
                self._t("translate_pick_language"),
                reply_markup=keyboard,
            )

    # ── Callbacks ─────────────────────────────────────────────────────────────

    async def cb_select_lang(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """User selected a language from the picker."""
        query = update.callback_query
        lang_code = query.data[len(_CB_LANG_PREFIX):]
        await query.answer()

        owner_id = self._owner_id(update)
        await self._settings.set(owner_id, TARGET_LANGUAGE_KEY, lang_code)

        pending_text = context.user_data.pop(self._PENDING_TEXT, "")
        if pending_text:
            await query.delete_message()
            await self._do_translate(update, pending_text, lang_code)
        else:
            context.user_data[self._AWAITING_TEXT] = True
            await query.edit_message_text(self._t("translate_ask_text"))

    async def cb_change_lang(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """User tapped 'Change language' — reset preference and show picker."""
        query = update.callback_query
        await query.answer()
        owner_id = self._owner_id(update)
        await self._settings.set(owner_id, TARGET_LANGUAGE_KEY, "")
        keyboard = await self._build_lang_keyboard()
        await query.edit_message_text(
            self._t("translate_pick_language"),
            reply_markup=keyboard,
        )

    # ── Text input ────────────────────────────────────────────────────────────

    async def handle_text_input(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Process plain text when the bot is awaiting text to translate."""
        if not context.user_data.pop(self._AWAITING_TEXT, False):
            return

        owner_id = self._owner_id(update)
        target_lang = await self._get_target_lang(owner_id)
        if not target_lang:
            keyboard = await self._build_lang_keyboard()
            await update.message.reply_text(
                self._t("translate_pick_language"),
                reply_markup=keyboard,
            )
            return

        text = update.message.text or ""
        await self._do_translate(update, text, target_lang)
