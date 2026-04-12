from sqlalchemy import BigInteger, String
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class BotSetting(Base):
    """Generic key-value store for per-owner bot settings (e.g. translation_enabled).

    Composite PK: (owner_chat_id, key).
    """

    __tablename__ = "bot_settings"

    # The bot-chat ID of the owner who owns this setting.
    owner_chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(String(255))

    def __repr__(self) -> str:
        return f"BotSetting(owner={self.owner_chat_id}, key={self.key!r}, value={self.value!r})"
