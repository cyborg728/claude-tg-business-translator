"""Postgres implementation of :class:`AbstractDatabase`.

Structurally identical to :class:`SqliteDatabase`, minus SQLite-specific
connect-args: no ``check_same_thread`` pragma, no filesystem ``makedirs``. The
async driver is ``asyncpg`` (via :func:`sqlalchemy.ext.asyncio.create_async_engine`);
sync/migration flows use ``psycopg``, but that's handled by Alembic, not here.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from ..interfaces.database import AbstractDatabase
from .repositories import (
    PostgresBusinessConnectionRepository,
    PostgresKvStoreRepository,
    PostgresMessageMappingRepository,
    PostgresUserRepository,
)


class PostgresDatabase(AbstractDatabase):
    """Async SQLAlchemy + Postgres concrete database."""

    def __init__(
        self,
        database_url: str,
        *,
        pool_size: int = 5,
        max_overflow: int = 10,
        pool_pre_ping: bool = True,
    ) -> None:
        self._database_url = database_url
        self._pool_size = pool_size
        self._max_overflow = max_overflow
        self._pool_pre_ping = pool_pre_ping

        self._engine: AsyncEngine | None = None
        self._session_factory: async_sessionmaker[AsyncSession] | None = None

        # Repositories are lazily built once the engine is ready.
        self._users: PostgresUserRepository | None = None
        self._business: PostgresBusinessConnectionRepository | None = None
        self._messages: PostgresMessageMappingRepository | None = None
        self._kv: PostgresKvStoreRepository | None = None

    # ── Lifecycle ─────────────────────────────────────────────────────────────
    async def connect(self) -> None:
        self._engine = create_async_engine(
            self._database_url,
            echo=False,
            pool_size=self._pool_size,
            max_overflow=self._max_overflow,
            pool_pre_ping=self._pool_pre_ping,
        )
        self._session_factory = async_sessionmaker(
            bind=self._engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )
        self._users = PostgresUserRepository(self._session_factory)
        self._business = PostgresBusinessConnectionRepository(self._session_factory)
        self._messages = PostgresMessageMappingRepository(self._session_factory)
        self._kv = PostgresKvStoreRepository(self._session_factory)

    async def disconnect(self) -> None:
        if self._engine is not None:
            await self._engine.dispose()
            self._engine = None
            self._session_factory = None

    # ── Repositories ──────────────────────────────────────────────────────────
    def _require(self, repo):
        if repo is None:
            raise RuntimeError("PostgresDatabase.connect() was not called.")
        return repo

    @property
    def users(self) -> PostgresUserRepository:
        return self._require(self._users)

    @property
    def business_connections(self) -> PostgresBusinessConnectionRepository:
        return self._require(self._business)

    @property
    def message_mappings(self) -> PostgresMessageMappingRepository:
        return self._require(self._messages)

    @property
    def kv(self) -> PostgresKvStoreRepository:
        return self._require(self._kv)
