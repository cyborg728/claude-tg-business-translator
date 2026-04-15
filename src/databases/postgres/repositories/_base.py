"""Session-factory helper shared by all Postgres repositories.

Identical control-flow to :class:`_SqliteRepositoryBase`: acquire a session
from the pool, commit on success, rollback on exception. Pulled into its own
module so every repository file stays under 50 LoC.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


class _PostgresRepositoryBase:
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
