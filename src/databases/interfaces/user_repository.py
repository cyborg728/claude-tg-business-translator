"""User repository interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime


@dataclass(slots=True)
class UserDTO:
    """Backend-agnostic user representation passed between layers."""

    id: str                       # UUIDv7
    telegram_user_id: int
    username: str | None
    first_name: str
    last_name: str | None
    language_code: str | None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class IUserRepository(ABC):
    @abstractmethod
    async def upsert(self, user: UserDTO) -> UserDTO: ...

    @abstractmethod
    async def get_by_telegram_id(self, telegram_user_id: int) -> UserDTO | None: ...

    @abstractmethod
    async def set_language(self, telegram_user_id: int, language_code: str) -> None: ...
