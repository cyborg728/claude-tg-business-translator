"""Business-connection repository interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime


@dataclass(slots=True)
class BusinessConnectionDTO:
    id: str                       # UUIDv7 primary key
    connection_id: str            # Telegram business_connection_id
    owner_telegram_user_id: int
    is_enabled: bool
    created_at: datetime | None = None
    updated_at: datetime | None = None


class IBusinessConnectionRepository(ABC):
    @abstractmethod
    async def upsert(self, record: BusinessConnectionDTO) -> BusinessConnectionDTO: ...

    @abstractmethod
    async def get(self, connection_id: str) -> BusinessConnectionDTO | None: ...

    @abstractmethod
    async def set_enabled(self, connection_id: str, enabled: bool) -> None: ...

    @abstractmethod
    async def delete(self, connection_id: str) -> None: ...
