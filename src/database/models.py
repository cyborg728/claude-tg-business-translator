from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import BigInteger, Boolean, Column, DateTime, Integer, String, Text
from sqlalchemy.orm import declared_attr
from sqlmodel import Field, SQLModel


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class CreatedAtMixin:
    """Adds a ``created_at`` column set once on INSERT."""

    @declared_attr
    def created_at(cls) -> Column:
        return Column(DateTime, default=_utcnow, nullable=False)


class TimestampMixin(CreatedAtMixin):
    """Adds ``created_at`` (INSERT) and ``updated_at`` (INSERT + UPDATE) columns."""

    @declared_attr
    def updated_at(cls) -> Column:
        return Column(DateTime, default=_utcnow, onupdate=_utcnow, nullable=False)


class BusinessConnectionRecord(TimestampMixin, SQLModel, table=True):
    """Stores Telegram Business Connection events."""

    __tablename__ = "business_connections"

    connection_id: str = Field(sa_column=Column(String(255), primary_key=True))
    owner_user_id: int = Field(sa_column=Column(BigInteger, nullable=False))
    # Chat between the bot and the business-account user (for DM notifications).
    owner_chat_id: int = Field(sa_column=Column(BigInteger, nullable=False))
    is_enabled: bool = Field(default=True, sa_column=Column(Boolean, default=True, nullable=False))

    def __repr__(self) -> str:
        return (
            f"BusinessConnectionRecord(id={self.connection_id!r}, "
            f"owner={self.owner_user_id}, enabled={self.is_enabled})"
        )


class UserRecord(TimestampMixin, SQLModel, table=True):
    """Caches Telegram user info and their detected language."""

    __tablename__ = "users"

    user_id: int = Field(sa_column=Column(BigInteger, primary_key=True))
    username: Optional[str] = Field(default=None, sa_column=Column(String(255), nullable=True))
    first_name: str = Field(sa_column=Column(String(255), nullable=False))
    last_name: Optional[str] = Field(default=None, sa_column=Column(String(255), nullable=True))
    # ISO 639-1 code from Telegram profile or auto-detected via Gemini.
    language_code: Optional[str] = Field(
        default=None, sa_column=Column(String(10), nullable=True)
    )

    @property
    def full_name(self) -> str:
        if self.last_name:
            return f"{self.first_name} {self.last_name}"
        return self.first_name

    def __repr__(self) -> str:
        return f"UserRecord(id={self.user_id}, name={self.first_name!r})"


class MessageMapping(CreatedAtMixin, SQLModel, table=True):
    """Maps each bot-notification message to its business-conversation context.

    When the owner replies to a notification message the bot sent, this table
    lets the bot look up which business connection and user chat to forward the
    translated reply to.
    """

    __tablename__ = "message_mappings"

    id: Optional[int] = Field(
        default=None, sa_column=Column(Integer, primary_key=True, autoincrement=True)
    )

    # Business connection under which the original user message arrived.
    business_connection_id: str = Field(sa_column=Column(String(255), nullable=False))

    # Telegram user who sent the original message.
    user_id: int = Field(sa_column=Column(BigInteger, nullable=False))

    # Chat ID used to send a reply back to the user via the business connection.
    user_chat_id: int = Field(sa_column=Column(BigInteger, nullable=False))

    # Message ID of the user's original message (in the user's chat).
    original_message_id: int = Field(sa_column=Column(Integer, nullable=False))

    # Message ID of the notification the bot sent to the owner's DM chat.
    notification_message_id: int = Field(
        sa_column=Column(Integer, nullable=False, index=True)
    )

    original_text: str = Field(sa_column=Column(Text, nullable=False))
    translated_text: str = Field(sa_column=Column(Text, nullable=False))

    # Language of the user (to translate owner replies back into).
    user_language: Optional[str] = Field(
        default=None, sa_column=Column(String(10), nullable=True)
    )

    def __repr__(self) -> str:
        return (
            f"MessageMapping(id={self.id}, "
            f"notification_msg={self.notification_message_id}, "
            f"user={self.user_id})"
        )


class AllowedUser(SQLModel, table=True):
    """Username whitelist per owner: only translate messages from users in this table.

    If the table has no entries for a given owner, translation applies to nobody.
    Composite PK: (owner_chat_id, username).
    """

    __tablename__ = "allowed_users"

    # The bot-chat ID of the owner who manages this whitelist.
    owner_chat_id: int = Field(sa_column=Column(BigInteger, primary_key=True))
    # Stored in lowercase, without the leading @.
    username: str = Field(sa_column=Column(String(32), primary_key=True))
    added_at: Optional[datetime] = Field(
        default=None, sa_column=Column(DateTime, default=_utcnow, nullable=False)
    )

    def __repr__(self) -> str:
        return f"AllowedUser(owner={self.owner_chat_id}, username={self.username!r})"


class BotSetting(SQLModel, table=True):
    """Generic key-value store for per-owner bot settings (e.g. translation_enabled).

    Composite PK: (owner_chat_id, key).
    """

    __tablename__ = "bot_settings"

    # The bot-chat ID of the owner who owns this setting.
    owner_chat_id: int = Field(sa_column=Column(BigInteger, primary_key=True))
    key: str = Field(sa_column=Column(String(64), primary_key=True))
    value: str = Field(sa_column=Column(String(255), nullable=False))

    def __repr__(self) -> str:
        return f"BotSetting(owner={self.owner_chat_id}, key={self.key!r}, value={self.value!r})"


class AuthorizedUser(SQLModel, table=True):
    """Users (other than the main owner) who are allowed to use the bot.

    The main owner manages this list via /translator → Access.
    Stored by username (lowercase, no @).
    """

    __tablename__ = "authorized_users"

    username: str = Field(sa_column=Column(String(32), primary_key=True))
    added_at: Optional[datetime] = Field(
        default=None, sa_column=Column(DateTime, default=_utcnow, nullable=False)
    )

    def __repr__(self) -> str:
        return f"AuthorizedUser(username={self.username!r})"


class Language(SQLModel, table=True):
    """Available translation target languages.

    code    — ISO 639-1 code (e.g. "ru", "en", "de").
    name_key — i18n key used to look up the localised language name (e.g. "lang_ru").
    """

    __tablename__ = "languages"

    code: str = Field(sa_column=Column(String(10), primary_key=True))
    name_key: str = Field(sa_column=Column(String(64), nullable=False))

    def __repr__(self) -> str:
        return f"Language(code={self.code!r}, name_key={self.name_key!r})"
