from abc import abstractmethod

from sqlalchemy import select

from ..models import AuthorizedUser
from .base import BaseRepository


class IAuthorizedUserRepository(BaseRepository[AuthorizedUser]):
    """Interface for the authorized-users repository (bot access control)."""

    @abstractmethod
    async def add(self, username: str) -> bool:
        """Grant access to *username*.  Returns False if already present."""
        ...

    @abstractmethod
    async def remove(self, username: str) -> bool:
        """Revoke access from *username*.  Returns False if not found."""
        ...

    @abstractmethod
    async def list_all(self) -> list[str]:
        """Return all authorized usernames (lowercase, no @)."""
        ...

    @abstractmethod
    async def exists(self, username: str) -> bool:
        """Return True if *username* has access."""
        ...


class AuthorizedUserRepository(IAuthorizedUserRepository):
    """SQLite implementation of IAuthorizedUserRepository."""

    async def add(self, username: str) -> bool:
        async with self._session() as sess:
            if await sess.get(AuthorizedUser, username):
                return False
            sess.add(AuthorizedUser(username=username))
            return True

    async def remove(self, username: str) -> bool:
        async with self._session() as sess:
            record = await sess.get(AuthorizedUser, username)
            if record is None:
                return False
            await sess.delete(record)
            return True

    async def list_all(self) -> list[str]:
        async with self._session() as sess:
            result = await sess.execute(select(AuthorizedUser.username))
            return [row[0] for row in result.all()]

    async def exists(self, username: str) -> bool:
        async with self._session() as sess:
            return await sess.get(AuthorizedUser, username) is not None
