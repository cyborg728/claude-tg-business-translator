import logging
import re
from datetime import datetime, timezone

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from src.config import Settings
from src.database.repositories.bot_setting import IBotSettingRepository
from src.i18n import Translator

logger = logging.getLogger(__name__)

# Callback-data constants.
_CB_SETTINGS_LANG = "settings:lang"
_CB_SETTINGS_TZ = "settings:tz"
_CB_SETTINGS_LANG_PREFIX = "settings:lang:"
_CB_SETTINGS_BACK = "settings:back"

# BotSetting keys.
BOT_LOCALE_KEY = "bot_locale"
TIMEZONE_OFFSET_KEY = "timezone_offset"

# Locales available for the bot interface.
_SUPPORTED_LOCALES: dict[str, str] = {
    "ru": "Русский 🇷🇺",
    "en": "English 🇬🇧",
}


class SettingsHandlers:
    """Handles /settings command and related inline-keyboard callbacks.

    Flow
    ----
    /settings → main settings menu
      🌐 Language → locale picker (one button per supported locale)
        <locale>  → save bot_locale, switch global translator, back to menu
      🕐 Timezone → ask user to send current time (HH:MM)
        <time>    → calculate UTC offset in minutes, save timezone_offset, confirm
    """

    _AWAITING_TIME = "settings_awaiting_time"

    def __init__(
        self,
        settings: Settings,
        translator: Translator,
        bot_setting_repo: IBotSettingRepository,
    ) -> None:
        self._settings = settings
        self._global_translator = translator  # shared with all other handlers
        self._bot_settings = bot_setting_repo
        # Pre-load all locale variants so we can render the menu in any language.
        self._translators: dict[str, Translator] = {
            locale: Translator(locale) for locale in _SUPPORTED_LOCALES
        }

    # ── Helpers ───────────────────────────────────────────────────────────────

    async def _t(self, owner_chat_id: int) -> Translator:
        """Return the translator for the owner's stored locale."""
        locale = await self._bot_settings.get(
            owner_chat_id, BOT_LOCALE_KEY, self._settings.locale
        )
        return self._translators.get(locale) or self._translators.get(
            self._settings.locale, next(iter(self._translators.values()))
        )

    async def _build_main_menu(
        self, owner_chat_id: int
    ) -> tuple[str, InlineKeyboardMarkup]:
        t = await self._t(owner_chat_id)
        locale = await self._bot_settings.get(
            owner_chat_id, BOT_LOCALE_KEY, self._settings.locale
        )
        tz_raw = await self._bot_settings.get(owner_chat_id, TIMEZONE_OFFSET_KEY, "")

        lang_display = _SUPPORTED_LOCALES.get(locale, locale)

        if tz_raw:
            offset_min = int(tz_raw)
            sign = "+" if offset_min >= 0 else "-"
            h, m = divmod(abs(offset_min), 60)
            tz_display = f"GMT{sign}{h:02d}:{m:02d}"
        else:
            tz_display = t("settings_tz_not_set")

        text = (
            t("settings_menu_title") + "\n\n"
            + t("settings_current_lang", lang=lang_display) + "\n"
            + t("settings_current_tz", tz=tz_display)
        )
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton(t("settings_btn_lang"), callback_data=_CB_SETTINGS_LANG)],
            [InlineKeyboardButton(t("settings_btn_tz"), callback_data=_CB_SETTINGS_TZ)],
        ])
        return text, keyboard

    @staticmethod
    def _parse_offset(user_input: str) -> int | None:
        """Parse HH:MM sent by the user and return UTC offset in minutes.

        Algorithm:
          1. Parse the user's local time.
          2. Subtract the current UTC time.
          3. Normalise to the valid range −720 … +840 minutes
             (UTC−12 … UTC+14 — the full range of real-world timezones).
          4. Round to the nearest 30 minutes
             (handles half-hour timezones like UTC+5:30, UTC+9:30).
        """
        match = re.match(r"^(\d{1,2}):(\d{2})$", user_input.strip())
        if not match:
            return None
        h, m = int(match.group(1)), int(match.group(2))
        if h > 23 or m > 59:
            return None

        utc_now = datetime.now(timezone.utc)
        user_minutes = h * 60 + m
        utc_minutes = utc_now.hour * 60 + utc_now.minute

        offset = user_minutes - utc_minutes
        # Normalise to the valid range.
        if offset > 840:
            offset -= 1440
        elif offset < -720:
            offset += 1440
        # Round to nearest 30-minute slot.
        return round(offset / 30) * 30

    # ── /settings command ─────────────────────────────────────────────────────

    async def cmd_settings(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        owner_id = update.effective_chat.id
        text, keyboard = await self._build_main_menu(owner_id)
        await update.message.reply_text(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)

    # ── Inline-keyboard callbacks ─────────────────────────────────────────────

    async def cb_lang(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Show the locale picker."""
        query = update.callback_query
        await query.answer()
        owner_id = update.effective_chat.id
        t = await self._t(owner_id)

        buttons = [
            [InlineKeyboardButton(name, callback_data=f"{_CB_SETTINGS_LANG_PREFIX}{code}")]
            for code, name in _SUPPORTED_LOCALES.items()
        ]
        buttons.append(
            [InlineKeyboardButton(t("settings_btn_back"), callback_data=_CB_SETTINGS_BACK)]
        )
        await query.edit_message_text(
            t("settings_pick_lang"),
            reply_markup=InlineKeyboardMarkup(buttons),
        )

    async def cb_set_lang(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Save the chosen locale and propagate it to all handlers."""
        query = update.callback_query
        locale = query.data[len(_CB_SETTINGS_LANG_PREFIX):]
        await query.answer()

        if locale not in _SUPPORTED_LOCALES:
            return

        owner_id = update.effective_chat.id
        await self._bot_settings.set(owner_id, BOT_LOCALE_KEY, locale)
        # Switch the shared Translator so all other handlers immediately
        # start responding in the new language.
        self._global_translator.switch_locale(locale)

        text, keyboard = await self._build_main_menu(owner_id)
        await query.edit_message_text(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)

    async def cb_tz(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Ask the user for their current local time."""
        query = update.callback_query
        await query.answer()
        owner_id = update.effective_chat.id
        t = await self._t(owner_id)

        context.user_data[self._AWAITING_TIME] = True
        await query.edit_message_text(t("settings_ask_time"), parse_mode=ParseMode.HTML)

    async def cb_back(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        await query.answer()
        owner_id = update.effective_chat.id
        text, keyboard = await self._build_main_menu(owner_id)
        await query.edit_message_text(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)

    # ── Stateful text-input handler ────────────────────────────────────────────

    async def handle_time_input(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Accept HH:MM from the user, calculate the UTC offset, and save it."""
        if not context.user_data.get(self._AWAITING_TIME):
            return
        context.user_data.pop(self._AWAITING_TIME)

        owner_id = update.effective_chat.id
        t = await self._t(owner_id)
        raw = (update.message.text or "").strip()

        offset = self._parse_offset(raw)
        if offset is None:
            await update.message.reply_text(
                t("settings_invalid_time"), parse_mode=ParseMode.HTML
            )
            return

        await self._bot_settings.set(owner_id, TIMEZONE_OFFSET_KEY, str(offset))

        sign = "+" if offset >= 0 else "-"
        h, m = divmod(abs(offset), 60)
        tz_str = f"GMT{sign}{h:02d}:{m:02d}"
        await update.message.reply_text(
            t("settings_tz_saved", tz=tz_str), parse_mode=ParseMode.HTML
        )

        logger.info("Owner %s set timezone offset to %s min (%s)", owner_id, offset, tz_str)
