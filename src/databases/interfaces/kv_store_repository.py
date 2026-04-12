"""Persistent key/value store interface (for durable small settings).

Ephemeral data should live in Redis (:mod:`src.cache`); this interface is for
values that MUST survive Redis flushes — e.g. per-user persisted preferences.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class IKvStoreRepository(ABC):
    @abstractmethod
    async def get(self, owner_id: int, key: str, default: str | None = None) -> str | None: ...

    @abstractmethod
    async def set(self, owner_id: int, key: str, value: str) -> None: ...

    @abstractmethod
    async def delete(self, owner_id: int, key: str) -> None: ...
