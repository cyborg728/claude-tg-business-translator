"""User repository interface."""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime


@dataclass(slots=True)
class UserDTO:
    """Backend-agnostic user representation passed between layers.

    ``id`` is assigned by the repository on insert (UUIDv7). Callers that
    build a DTO for an upsert should leave it ``None``; only ``_to_dto``
    reads from the model populates it with the real value.
    """

    telegram_user_id: int
    username: str | None
    first_name: str
    last_name: str | None
    language_code: str | None
    id: uuid.UUID | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class IUserRepository(ABC):
    @abstractmethod
    async def upsert(self, user: UserDTO) -> UserDTO: ...

    @abstractmethod
    async def get_by_telegram_id(self, telegram_user_id: int) -> UserDTO | None: ...

    @abstractmethod
    async def set_language(self, telegram_user_id: int, language_code: str) -> None: ...
