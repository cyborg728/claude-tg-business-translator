"""Message-mapping table (notification message → business conversation)."""

from __future__ import annotations

from sqlalchemy import BigInteger, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, CreatedAtMixin, UuidV7PrimaryKeyMixin


class MessageMappingModel(UuidV7PrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "message_mappings"

    business_connection_id: Mapped[str] = mapped_column(String(255), nullable=False)
    user_telegram_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    user_chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    original_message_id: Mapped[int] = mapped_column(Integer, nullable=False)
    notification_message_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    original_text: Mapped[str] = mapped_column(Text, nullable=False)
    user_language: Mapped[str | None] = mapped_column(String(10), nullable=True)
