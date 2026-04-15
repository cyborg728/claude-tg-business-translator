"""Postgres key/value-store repository — concurrency-safe upsert."""

from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from ...interfaces.kv_store_repository import IKvStoreRepository
from ..models import KvStoreModel
from ._base import _PostgresRepositoryBase


class PostgresKvStoreRepository(_PostgresRepositoryBase, IKvStoreRepository):
    async def get(self, owner_id: int, key: str, default: str | None = None) -> str | None:
        async with self._session() as sess:
            stmt = select(KvStoreModel).where(
                KvStoreModel.owner_id == owner_id, KvStoreModel.key == key
            )
            row = (await sess.execute(stmt)).scalar_one_or_none()
            return row.value if row else default

    async def set(self, owner_id: int, key: str, value: str) -> None:
        # Single-statement upsert keyed by (owner_id, key) — avoids the
        # read-modify-write race the SQLite backend accepts (SQLite has no
        # concurrent writers to worry about).
        async with self._session() as sess:
            stmt = (
                pg_insert(KvStoreModel)
                .values(owner_id=owner_id, key=key, value=value)
                .on_conflict_do_update(
                    constraint="uq_kv_store_owner_key",
                    set_={"value": value},
                )
            )
            await sess.execute(stmt)

    async def delete(self, owner_id: int, key: str) -> None:
        async with self._session() as sess:
            await sess.execute(
                delete(KvStoreModel).where(
                    KvStoreModel.owner_id == owner_id, KvStoreModel.key == key
                )
            )
