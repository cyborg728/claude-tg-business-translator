from abc import abstractmethod

from sqlalchemy import select

from ..models import AllowedUser
from .base import BaseRepository


class IAllowedUserRepository(BaseRepository[AllowedUser]):
    """Interface for the per-owner translation-whitelist repository."""

    @abstractmethod
    async def add(self, owner_chat_id: int, username: str) -> bool:
        """Add *username* to *owner*'s whitelist.  Returns False if already present."""
        ...

    @abstractmethod
    async def remove(self, owner_chat_id: int, username: str) -> bool:
        """Remove *username* from *owner*'s whitelist.  Returns False if not found."""
        ...

    @abstractmethod
    async def list_all(self, owner_chat_id: int) -> list[str]:
        """Return all whitelisted usernames for *owner* (lowercase, no @)."""
        ...

    @abstractmethod
    async def exists(self, owner_chat_id: int, username: str) -> bool:
        """Return True if *username* is in *owner*'s whitelist."""
        ...


class AllowedUserRepository(IAllowedUserRepository):
    """SQLite implementation of IAllowedUserRepository."""

    async def add(self, owner_chat_id: int, username: str) -> bool:
        async with self._session() as sess:
            if await sess.get(AllowedUser, (owner_chat_id, username)):
                return False
            sess.add(AllowedUser(owner_chat_id=owner_chat_id, username=username))
            return True

    async def remove(self, owner_chat_id: int, username: str) -> bool:
        async with self._session() as sess:
            record = await sess.get(AllowedUser, (owner_chat_id, username))
            if record is None:
                return False
            await sess.delete(record)
            return True

    async def list_all(self, owner_chat_id: int) -> list[str]:
        async with self._session() as sess:
            result = await sess.execute(
                select(AllowedUser.username).where(AllowedUser.owner_chat_id == owner_chat_id)
            )
            return [row[0] for row in result.all()]

    async def exists(self, owner_chat_id: int, username: str) -> bool:
        async with self._session() as sess:
            return await sess.get(AllowedUser, (owner_chat_id, username)) is not None
