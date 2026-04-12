"""SQLite implementation of :class:`AbstractDatabase`."""

from __future__ import annotations

import os

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from ..interfaces.database import AbstractDatabase
from .repositories import (
    SqliteBusinessConnectionRepository,
    SqliteKvStoreRepository,
    SqliteMessageMappingRepository,
    SqliteUserRepository,
)


class SqliteDatabase(AbstractDatabase):
    """Async SQLAlchemy + SQLite concrete database."""

    def __init__(self, database_url: str) -> None:
        self._database_url = database_url
        self._engine: AsyncEngine | None = None
        self._session_factory: async_sessionmaker[AsyncSession] | None = None

        # Repositories are lazily built once the engine is ready.
        self._users: SqliteUserRepository | None = None
        self._business: SqliteBusinessConnectionRepository | None = None
        self._messages: SqliteMessageMappingRepository | None = None
        self._kv: SqliteKvStoreRepository | None = None

    # ── Lifecycle ─────────────────────────────────────────────────────────────
    async def connect(self) -> None:
        if self._database_url.startswith("sqlite"):
            db_path = self._database_url.split("///", 1)[-1]
            if db_path and db_path != ":memory:":
                parent = os.path.dirname(db_path)
                if parent:
                    os.makedirs(parent, exist_ok=True)

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
        self._users = SqliteUserRepository(self._session_factory)
        self._business = SqliteBusinessConnectionRepository(self._session_factory)
        self._messages = SqliteMessageMappingRepository(self._session_factory)
        self._kv = SqliteKvStoreRepository(self._session_factory)

    async def disconnect(self) -> None:
        if self._engine is not None:
            await self._engine.dispose()
            self._engine = None
            self._session_factory = None

    # ── Repositories ──────────────────────────────────────────────────────────
    def _require(self, repo):
        if repo is None:
            raise RuntimeError("SqliteDatabase.connect() was not called.")
        return repo

    @property
    def users(self) -> SqliteUserRepository:
        return self._require(self._users)

    @property
    def business_connections(self) -> SqliteBusinessConnectionRepository:
        return self._require(self._business)

    @property
    def message_mappings(self) -> SqliteMessageMappingRepository:
        return self._require(self._messages)

    @property
    def kv(self) -> SqliteKvStoreRepository:
        return self._require(self._kv)
