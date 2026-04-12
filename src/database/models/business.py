from sqlalchemy import BigInteger, Boolean, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, CreatedAtMixin, TimestampMixin


class BusinessConnectionRecord(TimestampMixin, Base):
    """Stores Telegram Business Connection events."""

    __tablename__ = "business_connections"

    connection_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    # In Telegram, private chat_id == user_id, so one field is enough.
    owner_chat_id: Mapped[int] = mapped_column(BigInteger)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    def __repr__(self) -> str:
        return (
            f"BusinessConnectionRecord(id={self.connection_id!r}, "
            f"owner={self.owner_chat_id}, enabled={self.is_enabled})"
        )


class MessageMapping(CreatedAtMixin, Base):
    """Maps each bot-notification message to its business-conversation context.

    When the owner replies to a notification message the bot sent, this table
    lets the bot look up which business connection and user chat to forward the
    translated reply to.
    """

    __tablename__ = "message_mappings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # Business connection under which the original user message arrived.
    business_connection_id: Mapped[str] = mapped_column(String(255))

    # Telegram user who sent the original message.
    user_id: Mapped[int] = mapped_column(BigInteger)

    # Chat ID used to send a reply back to the user via the business connection.
    user_chat_id: Mapped[int] = mapped_column(BigInteger)

    # Message ID of the user's original message (in the user's chat).
    original_message_id: Mapped[int] = mapped_column(Integer)

    # Message ID of the notification the bot sent to the owner's DM chat.
    notification_message_id: Mapped[int] = mapped_column(Integer, index=True)

    original_text: Mapped[str] = mapped_column(Text)
    translated_text: Mapped[str] = mapped_column(Text)

    # Language of the user (to translate owner replies back into).
    user_language: Mapped[str | None] = mapped_column(String(10), nullable=True)

    def __repr__(self) -> str:
        return (
            f"MessageMapping(id={self.id}, "
            f"notification_msg={self.notification_message_id}, "
            f"user={self.user_id})"
        )
