"""Backend-agnostic database facade."""

from __future__ import annotations

from abc import ABC, abstractmethod

from .business_connection_repository import IBusinessConnectionRepository
from .kv_store_repository import IKvStoreRepository
from .message_mapping_repository import IMessageMappingRepository
from .user_repository import IUserRepository


class AbstractDatabase(ABC):
    """Exposes the repositories and manages the underlying connection.

    Concrete implementations (SQLite, Postgres, …) live in sibling packages.
    """

    # ── Lifecycle ─────────────────────────────────────────────────────────────
    @abstractmethod
    async def connect(self) -> None: ...

    @abstractmethod
    async def disconnect(self) -> None: ...

    # ── Repositories ──────────────────────────────────────────────────────────
    @property
    @abstractmethod
    def users(self) -> IUserRepository: ...

    @property
    @abstractmethod
    def business_connections(self) -> IBusinessConnectionRepository: ...

    @property
    @abstractmethod
    def message_mappings(self) -> IMessageMappingRepository: ...

    @property
    @abstractmethod
    def kv(self) -> IKvStoreRepository: ...
