from datetime import datetime, timezone

from sqlalchemy import BigInteger, Boolean, DateTime, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class BusinessConnectionRecord(Base):
    """Stores Telegram Business Connection events."""

    __tablename__ = "business_connections"

    connection_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    owner_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # Chat between the bot and the business-account user (for DM notifications).
    owner_chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_utcnow, onupdate=_utcnow, nullable=False
    )

    def __repr__(self) -> str:
        return (
            f"BusinessConnectionRecord(id={self.connection_id!r}, "
            f"owner={self.owner_user_id}, enabled={self.is_enabled})"
        )


class UserRecord(Base):
    """Caches Telegram user info and their detected language."""

    __tablename__ = "users"

    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    first_name: Mapped[str] = mapped_column(String(255), nullable=False)
    last_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # ISO 639-1 code from Telegram profile or auto-detected via Gemini.
    language_code: Mapped[str | None] = mapped_column(String(10), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_utcnow, onupdate=_utcnow, nullable=False
    )

    @property
    def full_name(self) -> str:
        if self.last_name:
            return f"{self.first_name} {self.last_name}"
        return self.first_name

    def __repr__(self) -> str:
        return f"UserRecord(id={self.user_id}, name={self.first_name!r})"


class MessageMapping(Base):
    """Maps each bot-notification message to its business-conversation context.

    When the owner replies to a notification message the bot sent, this table
    lets the bot look up which business connection and user chat to forward the
    translated reply to.
    """

    __tablename__ = "message_mappings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # Business connection under which the original user message arrived.
    business_connection_id: Mapped[str] = mapped_column(String(255), nullable=False)

    # Telegram user who sent the original message.
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)

    # Chat ID used to send a reply back to the user via the business connection.
    user_chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)

    # Message ID of the user's original message (in the user's chat).
    original_message_id: Mapped[int] = mapped_column(Integer, nullable=False)

    # Message ID of the notification the bot sent to the owner's DM chat.
    notification_message_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)

    original_text: Mapped[str] = mapped_column(Text, nullable=False)
    translated_text: Mapped[str] = mapped_column(Text, nullable=False)

    # Language of the user (to translate owner replies back into).
    user_language: Mapped[str | None] = mapped_column(String(10), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)

    def __repr__(self) -> str:
        return (
            f"MessageMapping(id={self.id}, "
            f"notification_msg={self.notification_message_id}, "
            f"user={self.user_id})"
        )


class AllowedUser(Base):
    """Username whitelist: only translate messages from users in this table.

    If the table is empty, translation applies to everyone.
    """

    __tablename__ = "allowed_users"

    # Stored in lowercase, without the leading @.
    username: Mapped[str] = mapped_column(String(32), primary_key=True)
    added_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)

    def __repr__(self) -> str:
        return f"AllowedUser(username={self.username!r})"


class BotSetting(Base):
    """Generic key-value store for bot-level settings (e.g. translation_enabled)."""

    __tablename__ = "bot_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(String(255), nullable=False)

    def __repr__(self) -> str:
        return f"BotSetting(key={self.key!r}, value={self.value!r})"
