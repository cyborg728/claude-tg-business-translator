from abc import abstractmethod

from sqlalchemy import select

from ..models import AllowedUser
from .base import BaseRepository


class IAllowedUserRepository(BaseRepository[AllowedUser]):
    """Interface for the translation-whitelist repository."""

    @abstractmethod
    async def add(self, username: str) -> bool:
        """Add *username* to the whitelist.  Returns False if already present."""
        ...

    @abstractmethod
    async def remove(self, username: str) -> bool:
        """Remove *username* from the whitelist.  Returns False if not found."""
        ...

    @abstractmethod
    async def list_all(self) -> list[str]:
        """Return all whitelisted usernames (lowercase, no @)."""
        ...

    @abstractmethod
    async def exists(self, username: str) -> bool:
        """Return True if *username* is in the whitelist."""
        ...


class AllowedUserRepository(IAllowedUserRepository):
    """SQLite implementation of IAllowedUserRepository."""

    async def add(self, username: str) -> bool:
        async with self._session() as sess:
            if await sess.get(AllowedUser, username):
                return False
            sess.add(AllowedUser(username=username))
            return True

    async def remove(self, username: str) -> bool:
        async with self._session() as sess:
            record = await sess.get(AllowedUser, username)
            if record is None:
                return False
            await sess.delete(record)
            return True

    async def list_all(self) -> list[str]:
        async with self._session() as sess:
            result = await sess.execute(select(AllowedUser.username))
            return [row[0] for row in result.all()]

    async def exists(self, username: str) -> bool:
        async with self._session() as sess:
            return await sess.get(AllowedUser, username) is not None
