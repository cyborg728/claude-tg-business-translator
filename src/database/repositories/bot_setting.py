from abc import abstractmethod

from ..models import BotSetting
from .base import BaseRepository

# Key used to store the translation-enabled flag.
TRANSLATION_ENABLED_KEY = "translation_enabled"


class IBotSettingRepository(BaseRepository[BotSetting]):
    """Interface for the per-owner key-value bot-settings store."""

    @abstractmethod
    async def get(self, owner_chat_id: int, key: str, default: str = "") -> str:
        """Return the stored value for *key* scoped to *owner*, or *default* if absent."""
        ...

    @abstractmethod
    async def set(self, owner_chat_id: int, key: str, value: str) -> None:
        """Insert or update *key* with *value* scoped to *owner*."""
        ...


class BotSettingRepository(IBotSettingRepository):
    """SQLite implementation of IBotSettingRepository."""

    async def get(self, owner_chat_id: int, key: str, default: str = "") -> str:
        async with self._session() as sess:
            record = await sess.get(BotSetting, (owner_chat_id, key))
            return record.value if record else default

    async def set(self, owner_chat_id: int, key: str, value: str) -> None:
        async with self._session() as sess:
            record = await sess.get(BotSetting, (owner_chat_id, key))
            if record is None:
                sess.add(BotSetting(owner_chat_id=owner_chat_id, key=key, value=value))
            else:
                record.value = value
