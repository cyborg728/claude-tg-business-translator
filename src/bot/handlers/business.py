"""Telegram Business handlers — connection events & incoming messages."""

from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import ContextTypes

from src.databases.interfaces.business_connection_repository import (
    BusinessConnectionDTO,
)
from src.databases.interfaces.message_mapping_repository import MessageMappingDTO

from ..deps import BotDeps
from ..utils import dto_from_telegram_user

logger = logging.getLogger(__name__)


class BusinessHandlers:
    def __init__(self, deps: BotDeps) -> None:
        self._deps = deps

    # ── business_connection update ───────────────────────────────────────────
    async def handle_connection(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        bc = update.business_connection
        if bc is None:
            return

        is_enabled = bool(bc.is_enabled)
        await self._deps.db.business_connections.upsert(
            BusinessConnectionDTO(
                id="",
                connection_id=bc.id,
                owner_telegram_user_id=bc.user.id if bc.user else 0,
                is_enabled=is_enabled,
            )
        )
        logger.info(
            "Business connection %s %s for user %s",
            bc.id,
            "enabled" if is_enabled else "disabled",
            bc.user.id if bc.user else "?",
        )

        if bc.user is None:
            return
        locale = self._deps.translator.pick_locale(bc.user.language_code)
        key = "business-connected" if is_enabled else "business-disconnected"
        try:
            await context.bot.send_message(
                chat_id=bc.user.id,
                text=self._deps.translator.gettext(key, locale=locale),
            )
        except Exception:  # pragma: no cover
            logger.exception("Failed to notify owner about business connection change")

    # ── business_message (from third-party user via business connection) ─────
    async def handle_business_message(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        bmsg = update.business_message
        if bmsg is None or bmsg.from_user is None:
            return

        # Remember the sender's info (including language_code).
        await self._deps.db.users.upsert(dto_from_telegram_user(bmsg.from_user))

        # Resolve the owner of this business connection.
        conn_id = bmsg.business_connection_id
        if not conn_id:
            return
        conn = await self._deps.db.business_connections.get(conn_id)
        if conn is None or not conn.is_enabled:
            logger.debug("Dropped business_message for unknown/disabled conn %s", conn_id)
            return

        owner_id = conn.owner_telegram_user_id
        owner_row = await self._deps.db.users.get_by_telegram_id(owner_id)
        owner_locale = self._deps.translator.pick_locale(
            owner_row.language_code if owner_row else None
        )

        header = self._deps.translator.gettext(
            "business-message-received", locale=owner_locale
        )
        text = bmsg.text or ""
        notification = await context.bot.send_message(
            chat_id=owner_id,
            text=f"{header}\n\n<b>{_escape(bmsg.from_user.full_name)}</b>: {_escape(text)}",
            parse_mode="HTML",
        )

        await self._deps.db.message_mappings.add(
            MessageMappingDTO(
                id="",
                business_connection_id=conn_id,
                user_telegram_id=bmsg.from_user.id,
                user_chat_id=bmsg.chat.id,
                original_message_id=bmsg.message_id,
                notification_message_id=notification.message_id,
                original_text=text,
                user_language=bmsg.from_user.language_code,
            )
        )


def _escape(text: str) -> str:
    """Minimal HTML escape for Telegram parse_mode=HTML."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
