import logging

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from src.config import Settings
from src.database.models import BusinessConnectionRecord, MessageMapping, UserRecord
from src.database.repositories import (
    IBusinessConnectionRepository,
    IMessageMappingRepository,
    IUserRepository,
)
from src.i18n import Translator
from src.services import TranslationService

logger = logging.getLogger(__name__)


class BusinessHandlers:
    """Handles all Telegram Business events.

    Flow
    ----
    1. ``handle_connection`` — fires when the owner connects/disconnects the bot
       from their Business account.  We persist the connection and notify the
       owner.

    2. ``handle_incoming_message`` — fires when a *user* sends a text message to
       the owner's business account.  We translate it into the owner's language
       and forward a notification to the owner's private chat with the bot.
       The notification message ID is stored so we can correlate replies.

    3. ``handle_owner_reply`` — fires when the owner replies *to a notification*
       in the private bot chat.  We translate the reply back to the user's
       language and send it via the business connection.
    """

    def __init__(
        self,
        settings: Settings,
        translator: Translator,
        translation_service: TranslationService,
        connection_repo: IBusinessConnectionRepository,
        message_repo: IMessageMappingRepository,
        user_repo: IUserRepository,
    ) -> None:
        self._settings = settings
        self._t = translator
        self._translation = translation_service
        self._connection_repo = connection_repo
        self._message_repo = message_repo
        self._user_repo = user_repo

    # ── Business connection ───────────────────────────────────────────────────

    async def handle_connection(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Persist the business connection and notify the owner."""
        bc = update.business_connection
        if bc is None:
            return

        logger.info(
            "Business connection update: id=%s enabled=%s user=%s",
            bc.id,
            bc.is_enabled,
            bc.user.id,
        )

        record = BusinessConnectionRecord(
            connection_id=bc.id,
            owner_user_id=bc.user.id,
            owner_chat_id=bc.user_chat_id,
            is_enabled=bc.is_enabled,
        )
        await self._connection_repo.upsert(record)

        if bc.is_enabled:
            text = self._t("business_connection_enabled", connection_id=bc.id)
        else:
            text = self._t("business_connection_disabled", connection_id=bc.id)

        await context.bot.send_message(
            chat_id=bc.user_chat_id,
            text=text,
            parse_mode=ParseMode.HTML,
        )

    # ── Incoming user message ─────────────────────────────────────────────────

    async def handle_incoming_message(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Translate a user's text message and forward it to the owner."""
        message = update.effective_message
        if message is None or not message.text:
            return

        sender = message.from_user
        if sender is None:
            return

        # Ignore messages coming *from* the owner inside the business chat.
        if sender.id == self._settings.owner_chat_id:
            return

        business_connection_id = message.business_connection_id
        if not business_connection_id:
            logger.warning("Business message received without business_connection_id — skipping")
            return

        # Persist / refresh user record.
        user_record = UserRecord(
            user_id=sender.id,
            username=sender.username,
            first_name=sender.first_name,
            last_name=sender.last_name,
            language_code=sender.language_code,
        )
        await self._user_repo.upsert(user_record)

        # Determine the user's language (Telegram profile → Gemini detection fallback).
        user_language = sender.language_code
        if not user_language:
            user_language = await self._translation.detect_language(message.text)
            await self._user_repo.update_language(sender.id, user_language)

        # Translate to the owner's language.
        translated_text = await self._translate_safely(
            message.text,
            target_language=self._settings.owner_language,
        )

        # Build the notification text.
        if sender.username:
            contact = f"@{sender.username}"
        else:
            contact = f"ID: {sender.id}"

        full_name = sender.full_name
        notification_text = self._t(
            "new_user_message",
            name=full_name,
            contact=contact,
            original=message.text,
            target_lang=self._settings.owner_language.upper(),
            translation=translated_text,
        )

        # Send notification to the owner.
        notification = await context.bot.send_message(
            chat_id=self._settings.owner_chat_id,
            text=notification_text,
            parse_mode=ParseMode.HTML,
        )

        # Store the mapping so we can correlate owner replies later.
        mapping = MessageMapping(
            business_connection_id=business_connection_id,
            user_id=sender.id,
            user_chat_id=message.chat.id,
            original_message_id=message.message_id,
            notification_message_id=notification.message_id,
            original_text=message.text,
            translated_text=translated_text,
            user_language=user_language,
        )
        await self._message_repo.save(mapping)

        logger.info(
            "Forwarded message from user %s to owner (notification_msg=%s)",
            sender.id,
            notification.message_id,
        )

    # ── Owner reply ───────────────────────────────────────────────────────────

    async def handle_owner_reply(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Translate the owner's reply and send it to the user via business connection."""
        message = update.effective_message
        if message is None or not message.text or message.reply_to_message is None:
            return

        replied_to_id = message.reply_to_message.message_id

        # Look up the stored mapping.
        mapping = await self._message_repo.get_by_notification_id(replied_to_id)
        if mapping is None:
            logger.debug(
                "Owner replied to msg %s which has no mapping — ignoring", replied_to_id
            )
            await message.reply_text(self._t("reply_not_found"))
            return

        # Translate from the owner's language to the user's language.
        target_lang = mapping.user_language or "en"
        translated_reply = await self._translate_safely(
            message.text,
            target_language=target_lang,
            source_language=self._settings.owner_language,
        )

        if translated_reply is None:
            await message.reply_text(
                self._t("translation_error", error="translation returned empty result")
            )
            return

        # Send the translated reply to the user via the business connection.
        await context.bot.send_message(
            chat_id=mapping.user_chat_id,
            text=translated_reply,
            business_connection_id=mapping.business_connection_id,
        )

        # Confirm delivery to the owner.
        await message.reply_text(
            self._t("reply_sent"),
            parse_mode=ParseMode.HTML,
        )

        logger.info(
            "Sent translated reply from owner to user %s via connection %s",
            mapping.user_id,
            mapping.business_connection_id,
        )

    # ── Helpers ───────────────────────────────────────────────────────────────

    async def _translate_safely(
        self,
        text: str,
        target_language: str,
        source_language: str | None = None,
    ) -> str:
        """Translate *text* and return an error string on failure (never raises)."""
        try:
            return await self._translation.translate(
                text,
                target_language=target_language,
                source_language=source_language,
            )
        except Exception as exc:
            logger.error("Translation failed: %s", exc)
            return f"[{self._t('translation_error', error=str(exc))}]"
