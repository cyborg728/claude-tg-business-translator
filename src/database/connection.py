import os
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from sqlmodel import SQLModel


class Database:
    """Manages the async SQLAlchemy engine and session factory."""

    def __init__(self, database_url: str) -> None:
        self._database_url = database_url
        self._engine: AsyncEngine | None = None
        self._session_factory: async_sessionmaker[AsyncSession] | None = None

    async def connect(self) -> None:
        """Create the engine, session factory, and all tables."""
        # Ensure the parent directory for the SQLite file exists.
        if self._database_url.startswith("sqlite"):
            db_path = self._database_url.split("///", 1)[-1]
            if db_path and db_path != ":memory:":
                os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)

        self._engine = create_async_engine(
            self._database_url,
            echo=False,
            connect_args={"check_same_thread": False},
        )
        self._session_factory = async_sessionmaker(
            bind=self._engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )

        async with self._engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)

    async def disconnect(self) -> None:
        """Dispose the engine and release all connections."""
        if self._engine is not None:
            await self._engine.dispose()
            self._engine = None

    @asynccontextmanager
    async def session(self) -> AsyncGenerator[AsyncSession, None]:
        """Yield an AsyncSession with automatic commit/rollback."""
        if self._session_factory is None:
            raise RuntimeError("Database.connect() was not called.")
        async with self._session_factory() as sess:
            try:
                yield sess
                await sess.commit()
            except Exception:
                await sess.rollback()
                raise

    def get_session_factory(self) -> async_sessionmaker[AsyncSession]:
        if self._session_factory is None:
            raise RuntimeError("Database.connect() was not called.")
        return self._session_factory
