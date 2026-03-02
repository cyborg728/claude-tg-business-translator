import logging
import re

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from src.database.repositories.allowed_user import IAllowedUserRepository
from src.database.repositories.bot_setting import (
    IBotSettingRepository,
    TRANSLATION_ENABLED_KEY,
)
from src.i18n import Translator

logger = logging.getLogger(__name__)

# Callback-data prefixes used by this handler.
_CB_ADD = "tm:add"
_CB_LIST = "tm:list"
_CB_DEL_PREFIX = "tm:del:"
_CB_TOGGLE = "tm:toggle"
_CB_BACK = "tm:back"


class TranslatorMenuHandlers:
    """Handles /translator command and all related inline-keyboard callbacks.

    Flow
    ----
    /translator → main menu (status, 3 buttons)
      ➕ Добавить  → edit message to "enter username", set awaiting-state
        <text>     → save username, reply with confirmation
      🗑 Удалить   → edit message to list of users (each is a delete button)
        <user btn> → remove user, refresh list in-place
      ⏸/▶️ Toggle  → flip translation_enabled, refresh menu in-place
    """

    # key in context.user_data indicating the bot is waiting for a username
    _AWAITING = "tm_awaiting_username"

    def __init__(
        self,
        translator: Translator,
        allowed_user_repo: IAllowedUserRepository,
        bot_setting_repo: IBotSettingRepository,
    ) -> None:
        self._t = translator
        self._users = allowed_user_repo
        self._settings = bot_setting_repo

    # ── Helpers ───────────────────────────────────────────────────────────────

    async def _is_enabled(self) -> bool:
        return await self._settings.get(TRANSLATION_ENABLED_KEY, "true") == "true"

    async def _build_main_menu(self) -> tuple[str, InlineKeyboardMarkup]:
        enabled = await self._is_enabled()
        users = await self._users.list_all()

        status = self._t("translator_status_on" if enabled else "translator_status_off")
        whitelist = (
            self._t("translator_whitelist_count", count=len(users))
            if users
            else self._t("translator_whitelist_all")
        )
        text = self._t("translator_menu_title") + "\n\n" + status + "\n" + whitelist

        toggle_key = "translator_btn_disable" if enabled else "translator_btn_enable"
        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(self._t("translator_btn_add"), callback_data=_CB_ADD),
                    InlineKeyboardButton(self._t("translator_btn_remove"), callback_data=_CB_LIST),
                ],
                [InlineKeyboardButton(self._t(toggle_key), callback_data=_CB_TOGGLE)],
            ]
        )
        return text, keyboard

    def _remove_keyboard(self, users: list[str]) -> InlineKeyboardMarkup:
        back_btn = InlineKeyboardButton(self._t("translator_btn_back"), callback_data=_CB_BACK)
        if not users:
            return InlineKeyboardMarkup([[back_btn]])
        buttons = [
            [InlineKeyboardButton(f"@{u}", callback_data=f"{_CB_DEL_PREFIX}{u}")]
            for u in users
        ]
        buttons.append([back_btn])
        return InlineKeyboardMarkup(buttons)

    # ── Command ───────────────────────────────────────────────────────────────

    async def cmd_translator(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        text, keyboard = await self._build_main_menu()
        await update.message.reply_text(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)

    # ── Callbacks ─────────────────────────────────────────────────────────────

    async def cb_add(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Set awaiting-state and ask the owner for a username."""
        query = update.callback_query
        await query.answer()
        context.user_data[self._AWAITING] = True
        await query.edit_message_text(self._t("translator_ask_username"))

    async def cb_list(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Show the current whitelist; each entry is a button that deletes that user."""
        query = update.callback_query
        await query.answer()
        users = await self._users.list_all()

        if not users:
            await query.edit_message_text(
                self._t("translator_list_empty"),
                reply_markup=self._remove_keyboard(users),
            )
            return

        await query.edit_message_text(
            self._t("translator_remove_prompt"),
            reply_markup=self._remove_keyboard(users),
        )

    async def cb_delete(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Remove the tapped user and refresh the remove-list in place."""
        query = update.callback_query
        # callback_data = "tm:del:<username>"
        username = query.data[len(_CB_DEL_PREFIX):]
        await query.answer()

        removed = await self._users.remove(username)
        result = self._t(
            "translator_removed" if removed else "translator_not_found",
            username=username,
        )

        users = await self._users.list_all()
        if not users:
            await query.edit_message_text(
                result + "\n\n" + self._t("translator_list_empty"),
                reply_markup=self._remove_keyboard(users),
            )
            return

        await query.edit_message_text(
            result + "\n\n" + self._t("translator_remove_prompt"),
            reply_markup=self._remove_keyboard(users),
        )

    async def cb_toggle(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Flip translation_enabled and refresh the main menu."""
        query = update.callback_query
        await query.answer()
        enabled = await self._is_enabled()
        await self._settings.set(TRANSLATION_ENABLED_KEY, "false" if enabled else "true")
        text, keyboard = await self._build_main_menu()
        await query.edit_message_text(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)

    async def cb_back(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Return to the main menu."""
        query = update.callback_query
        await query.answer()
        text, keyboard = await self._build_main_menu()
        await query.edit_message_text(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)

    # ── Username input ────────────────────────────────────────────────────────

    async def handle_username_input(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Process a plain-text message from the owner when awaiting a username."""
        if not context.user_data.pop(self._AWAITING, False):
            return

        raw = (update.message.text or "").strip().lstrip("@")
        if not re.fullmatch(r"[A-Za-z0-9_]{5,32}", raw):
            await update.message.reply_text(self._t("translator_invalid_username"))
            return

        username = raw.lower()
        added = await self._users.add(username)
        key = "translator_added" if added else "translator_already_exists"
        await update.message.reply_text(self._t(key, username=username))
