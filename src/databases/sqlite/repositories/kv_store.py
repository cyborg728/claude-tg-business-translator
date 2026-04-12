"""SQLite key/value-store repository."""

from __future__ import annotations

from sqlalchemy import delete, select

from ...interfaces.kv_store_repository import IKvStoreRepository
from ..models import KvStoreModel
from ._base import _SqliteRepositoryBase


class SqliteKvStoreRepository(_SqliteRepositoryBase, IKvStoreRepository):
    async def get(self, owner_id: int, key: str, default: str | None = None) -> str | None:
        async with self._session() as sess:
            stmt = select(KvStoreModel).where(
                KvStoreModel.owner_id == owner_id, KvStoreModel.key == key
            )
            row = (await sess.execute(stmt)).scalar_one_or_none()
            return row.value if row else default

    async def set(self, owner_id: int, key: str, value: str) -> None:
        async with self._session() as sess:
            stmt = select(KvStoreModel).where(
                KvStoreModel.owner_id == owner_id, KvStoreModel.key == key
            )
            row = (await sess.execute(stmt)).scalar_one_or_none()
            if row is None:
                sess.add(KvStoreModel(owner_id=owner_id, key=key, value=value))
            else:
                row.value = value

    async def delete(self, owner_id: int, key: str) -> None:
        async with self._session() as sess:
            await sess.execute(
                delete(KvStoreModel).where(
                    KvStoreModel.owner_id == owner_id, KvStoreModel.key == key
                )
            )
