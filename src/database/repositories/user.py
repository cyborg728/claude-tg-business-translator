from abc import abstractmethod

from ..models import UserRecord
from .base import BaseRepository


class IUserRepository(BaseRepository[UserRecord]):
    """Interface for user-record persistence."""

    @abstractmethod
    async def upsert(self, record: UserRecord) -> UserRecord:
        """Insert or update a user record."""
        ...

    @abstractmethod
    async def get(self, user_id: int) -> UserRecord | None:
        """Return the user record for the given Telegram user ID, or None."""
        ...

    @abstractmethod
    async def update_language(self, user_id: int, language_code: str) -> None:
        """Update the stored language code for a user."""
        ...


class UserRepository(IUserRepository):
    """SQLite implementation of IUserRepository."""

    async def upsert(self, record: UserRecord) -> UserRecord:
        async with self._session() as sess:
            existing = await sess.get(UserRecord, record.user_id)
            if existing is None:
                sess.add(record)
            else:
                existing.username = record.username
                existing.first_name = record.first_name
                existing.last_name = record.last_name
                # Only overwrite language_code if the new value is not None.
                if record.language_code is not None:
                    existing.language_code = record.language_code
            await sess.flush()
            return record

    async def get(self, user_id: int) -> UserRecord | None:
        async with self._session() as sess:
            return await sess.get(UserRecord, user_id)

    async def update_language(self, user_id: int, language_code: str) -> None:
        async with self._session() as sess:
            record = await sess.get(UserRecord, user_id)
            if record is not None:
                record.language_code = language_code
