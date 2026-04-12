"""Message-mapping repository interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime


@dataclass(slots=True)
class MessageMappingDTO:
    """Maps a bot notification message back to its business-conversation."""

    id: str                            # UUIDv7
    business_connection_id: str
    user_telegram_id: int
    user_chat_id: int
    original_message_id: int
    notification_message_id: int
    original_text: str
    user_language: str | None = None
    created_at: datetime | None = None


class IMessageMappingRepository(ABC):
    @abstractmethod
    async def add(self, mapping: MessageMappingDTO) -> MessageMappingDTO: ...

    @abstractmethod
    async def get_by_notification_id(
        self, notification_message_id: int
    ) -> MessageMappingDTO | None: ...
