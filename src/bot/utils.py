"""Small helpers shared between handlers."""

from __future__ import annotations

from telegram import User as TgUser

from src.databases.interfaces.user_repository import UserDTO


def dto_from_telegram_user(tg_user: TgUser) -> UserDTO:
    return UserDTO(
        id="",  # filled in by the repository via UUIDv7 default
        telegram_user_id=tg_user.id,
        username=tg_user.username,
        first_name=tg_user.first_name or "",
        last_name=tg_user.last_name,
        language_code=tg_user.language_code,
    )
