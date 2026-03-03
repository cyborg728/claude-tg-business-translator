import logging
import re

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from src.config import Settings
from src.database.repositories.allowed_user import IAllowedUserRepository
from src.database.repositories.authorized_user import IAuthorizedUserRepository
from src.database.repositories.bot_setting import (
    IBotSettingRepository,
    TRANSLATION_ENABLED_KEY,
)
from src.i18n import Translator

logger = logging.getLogger(__name__)

# Callback-data constants used by this handler.
_CB_ADD = "tm:add"
_CB_LIST = "tm:list"
_CB_DEL_PREFIX = "tm:del:"
_CB_TOGGLE = "tm:toggle"
_CB_BACK = "tm:back"
_CB_ACCESS = "tm:access"
_CB_ACCESS_ADD = "tm:access:add"
_CB_ACCESS_LIST = "tm:access:list"
_CB_ACCESS_DEL_PREFIX = "tm:access:del:"


class TranslatorMenuHandlers:
    """Handles /translator command and all related inline-keyboard callbacks.

    Flow
    ----
    /translator → main menu (status, whitelist count, buttons)
      ➕ Add      → ask for username, set awaiting-state
        <text>    → save to whitelist, confirm
      🗑 Remove   → show list of whitelisted users as delete buttons
        <user>    → remove from whitelist, refresh list
      ⏸/▶️ Toggle → flip translation_enabled, refresh menu
      👥 Access   → (owner only) access-management sub-menu
        ➕ Add    → ask for username to grant access
        🗑 Remove → show authorized users list as delete buttons
    """

    _AWAITING = "tm_awaiting_username"
    _AWAITING_AUTH = "tm_awaiting_auth_username"

    def __init__(
        self,
        settings: Settings,
        translator: Translator,
        allowed_user_repo: IAllowedUserRepository,
        bot_setting_repo: IBotSettingRepository,
        authorized_user_repo: IAuthorizedUserRepository,
    ) -> None:
        self._settings = settings
        self._t = translator
        self._users = allowed_user_repo
        self._bot_settings = bot_setting_repo
        self._auth_users = authorized_user_repo

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _owner_id(self, update: Update) -> int:
        return update.effective_chat.id

    def _is_main_owner(self, update: Update) -> bool:
        return update.effective_chat.id == self._settings.owner_chat_id

    async def _is_enabled(self, owner_chat_id: int) -> bool:
        return (
            await self._bot_settings.get(owner_chat_id, TRANSLATION_ENABLED_KEY, "true") == "true"
        )

    async def _build_main_menu(
        self, owner_chat_id: int, is_main_owner: bool
    ) -> tuple[str, InlineKeyboardMarkup]:
        enabled = await self._is_enabled(owner_chat_id)
        users = await self._users.list_all(owner_chat_id)

        status = self._t("translator_status_on" if enabled else "translator_status_off")
        whitelist = (
            self._t("translator_whitelist_count", count=len(users))
            if users
            else self._t("translator_whitelist_all")
        )
        text = self._t("translator_menu_title") + "\n\n" + status + "\n" + whitelist

        toggle_key = "translator_btn_disable" if enabled else "translator_btn_enable"
        rows = [
            [
                InlineKeyboardButton(self._t("translator_btn_add"), callback_data=_CB_ADD),
                InlineKeyboardButton(self._t("translator_btn_remove"), callback_data=_CB_LIST),
            ],
            [InlineKeyboardButton(self._t(toggle_key), callback_data=_CB_TOGGLE)],
        ]
        if is_main_owner:
            rows.append(
                [InlineKeyboardButton(self._t("access_btn"), callback_data=_CB_ACCESS)]
            )
        return text, InlineKeyboardMarkup(rows)

    def _remove_keyboard(self, users: list[str], del_prefix: str) -> InlineKeyboardMarkup:
        back_btn = InlineKeyboardButton(self._t("translator_btn_back"), callback_data=_CB_BACK)
        if not users:
            return InlineKeyboardMarkup([[back_btn]])
        buttons = [
            [InlineKeyboardButton(f"@{u}", callback_data=f"{del_prefix}{u}")]
            for u in users
        ]
        buttons.append([back_btn])
        return InlineKeyboardMarkup(buttons)

    def _access_keyboard(self, users: list[str]) -> InlineKeyboardMarkup:
        back_btn = InlineKeyboardButton(self._t("translator_btn_back"), callback_data=_CB_BACK)
        rows = [
            [
                InlineKeyboardButton(self._t("access_btn_add"), callback_data=_CB_ACCESS_ADD),
                InlineKeyboardButton(self._t("access_btn_remove"), callback_data=_CB_ACCESS_LIST),
            ]
        ]
        rows.append([back_btn])
        return InlineKeyboardMarkup(rows)

    # ── Command ───────────────────────────────────────────────────────────────

    async def cmd_translator(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        owner_id = self._owner_id(update)
        text, keyboard = await self._build_main_menu(owner_id, self._is_main_owner(update))
        await update.message.reply_text(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)

    # ── Whitelist callbacks ────────────────────────────────────────────────────

    async def cb_add(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        await query.answer()
        context.user_data[self._AWAITING] = True
        await query.edit_message_text(self._t("translator_ask_username"))

    async def cb_list(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        await query.answer()
        owner_id = self._owner_id(update)
        users = await self._users.list_all(owner_id)

        text = (
            self._t("translator_remove_prompt")
            if users
            else self._t("translator_list_empty")
        )
        await query.edit_message_text(
            text,
            reply_markup=self._remove_keyboard(users, _CB_DEL_PREFIX),
        )

    async def cb_delete(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        username = query.data[len(_CB_DEL_PREFIX):]
        await query.answer()
        owner_id = self._owner_id(update)

        removed = await self._users.remove(owner_id, username)
        result = self._t(
            "translator_removed" if removed else "translator_not_found",
            username=username,
        )

        users = await self._users.list_all(owner_id)
        suffix = (
            self._t("translator_list_empty") if not users else self._t("translator_remove_prompt")
        )
        await query.edit_message_text(
            result + "\n\n" + suffix,
            reply_markup=self._remove_keyboard(users, _CB_DEL_PREFIX),
        )

    async def cb_toggle(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        await query.answer()
        owner_id = self._owner_id(update)
        enabled = await self._is_enabled(owner_id)
        await self._bot_settings.set(
            owner_id, TRANSLATION_ENABLED_KEY, "false" if enabled else "true"
        )
        text, keyboard = await self._build_main_menu(owner_id, self._is_main_owner(update))
        await query.edit_message_text(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)

    async def cb_back(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        await query.answer()
        owner_id = self._owner_id(update)
        text, keyboard = await self._build_main_menu(owner_id, self._is_main_owner(update))
        await query.edit_message_text(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)

    # ── Access-management callbacks (owner only) ───────────────────────────────

    async def cb_access(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        await query.answer()
        if not self._is_main_owner(update):
            return
        users = await self._auth_users.list_all()
        text = self._t("access_menu_title")
        if users:
            text += "\n\n" + "\n".join(f"• @{u}" for u in users)
        await query.edit_message_text(
            text,
            reply_markup=self._access_keyboard(users),
            parse_mode=ParseMode.HTML,
        )

    async def cb_access_add(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        await query.answer()
        if not self._is_main_owner(update):
            return
        context.user_data[self._AWAITING_AUTH] = True
        await query.edit_message_text(self._t("access_ask_username"))

    async def cb_access_list(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        await query.answer()
        if not self._is_main_owner(update):
            return
        users = await self._auth_users.list_all()
        text = (
            self._t("access_remove_prompt") if users else self._t("access_list_empty")
        )
        await query.edit_message_text(
            text,
            reply_markup=self._remove_keyboard(users, _CB_ACCESS_DEL_PREFIX),
        )

    async def cb_access_delete(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        query = update.callback_query
        username = query.data[len(_CB_ACCESS_DEL_PREFIX):]
        await query.answer()
        if not self._is_main_owner(update):
            return

        removed = await self._auth_users.remove(username)
        result = self._t(
            "access_removed" if removed else "access_not_found",
            username=username,
        )

        users = await self._auth_users.list_all()
        suffix = (
            self._t("access_list_empty") if not users else self._t("access_remove_prompt")
        )
        await query.edit_message_text(
            result + "\n\n" + suffix,
            reply_markup=self._remove_keyboard(users, _CB_ACCESS_DEL_PREFIX),
        )

    # ── Text input dispatcher ─────────────────────────────────────────────────

    async def handle_username_input(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Handle plain text when awaiting a whitelist username or an access username."""
        if context.user_data.get(self._AWAITING):
            context.user_data.pop(self._AWAITING)
            await self._handle_whitelist_username(update)
        elif context.user_data.get(self._AWAITING_AUTH):
            context.user_data.pop(self._AWAITING_AUTH)
            await self._handle_auth_username(update)

    async def _handle_whitelist_username(self, update: Update) -> None:
        raw = (update.message.text or "").strip().lstrip("@")
        if not re.fullmatch(r"[A-Za-z0-9_]{5,32}", raw):
            await update.message.reply_text(self._t("translator_invalid_username"))
            return
        username = raw.lower()
        owner_id = self._owner_id(update)
        added = await self._users.add(owner_id, username)
        key = "translator_added" if added else "translator_already_exists"
        await update.message.reply_text(self._t(key, username=username))

    async def _handle_auth_username(self, update: Update) -> None:
        raw = (update.message.text or "").strip().lstrip("@")
        if not re.fullmatch(r"[A-Za-z0-9_]{5,32}", raw):
            await update.message.reply_text(self._t("translator_invalid_username"))
            return
        username = raw.lower()
        added = await self._auth_users.add(username)
        key = "access_added" if added else "access_already_exists"
        await update.message.reply_text(self._t(key, username=username))
