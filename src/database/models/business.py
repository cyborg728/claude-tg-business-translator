from sqlalchemy import BigInteger, Boolean, Column, Integer, String, Text

from sqlmodel import Field

from .base import Base, CreatedAtMixin, TimestampMixin


class BusinessConnectionRecord(TimestampMixin, Base, table=True):
    """Stores Telegram Business Connection events."""

    __tablename__ = "business_connections"

    connection_id: str = Field(sa_column=Column(String(255), primary_key=True))
    # In Telegram, private chat_id == user_id, so one field is enough.
    owner_chat_id: int = Field(sa_column=Column(BigInteger, nullable=False))
    is_enabled: bool = Field(default=True, sa_column=Column(Boolean, default=True, nullable=False))

    def __repr__(self) -> str:
        return (
            f"BusinessConnectionRecord(id={self.connection_id!r}, "
            f"owner={self.owner_chat_id}, enabled={self.is_enabled})"
        )


class MessageMapping(CreatedAtMixin, Base, table=True):
    """Maps each bot-notification message to its business-conversation context.

    When the owner replies to a notification message the bot sent, this table
    lets the bot look up which business connection and user chat to forward the
    translated reply to.
    """

    __tablename__ = "message_mappings"

    id: int | None = Field(
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
    user_language: str | None = Field(
        default=None, sa_column=Column(String(10), nullable=True)
    )

    def __repr__(self) -> str:
        return (
            f"MessageMapping(id={self.id}, "
            f"notification_msg={self.notification_message_id}, "
            f"user={self.user_id})"
        )
