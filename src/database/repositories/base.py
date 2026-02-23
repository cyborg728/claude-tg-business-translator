from abc import ABC, abstractmethod
from contextlib import asynccontextmanager
from typing import AsyncGenerator, Generic, TypeVar

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

T = TypeVar("T")


class BaseRepository(ABC, Generic[T]):
    """Abstract base for all repositories.

    Each concrete subclass receives the session factory from the ``Database``
    instance so that sessions are created per-operation and committed/rolled
    back automatically.
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    @asynccontextmanager
    async def _session(self) -> AsyncGenerator[AsyncSession, None]:
        async with self._session_factory() as sess:
            try:
                yield sess
                await sess.commit()
            except Exception:
                await sess.rollback()
                raise
