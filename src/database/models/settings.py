from sqlalchemy import BigInteger, Column, String

from sqlmodel import Field

from .base import Base


class BotSetting(Base, table=True):
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
