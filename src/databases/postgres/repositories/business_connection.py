"""Postgres business-connection repository."""

from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from ...interfaces.business_connection_repository import (
    BusinessConnectionDTO,
    IBusinessConnectionRepository,
)
from ..models import BusinessConnectionModel
from ._base import _PostgresRepositoryBase


def _to_dto(m: BusinessConnectionModel) -> BusinessConnectionDTO:
    return BusinessConnectionDTO(
        id=m.id,
        connection_id=m.connection_id,
        owner_telegram_user_id=m.owner_telegram_user_id,
        is_enabled=m.is_enabled,
        created_at=m.created_at,
        updated_at=m.updated_at,
    )


class PostgresBusinessConnectionRepository(
    _PostgresRepositoryBase, IBusinessConnectionRepository
):
    async def upsert(self, record: BusinessConnectionDTO) -> BusinessConnectionDTO:
        async with self._session() as sess:
            stmt = (
                pg_insert(BusinessConnectionModel)
                .values(
                    connection_id=record.connection_id,
                    owner_telegram_user_id=record.owner_telegram_user_id,
                    is_enabled=record.is_enabled,
                )
                .on_conflict_do_update(
                    constraint="uq_business_connections_conn_id",
                    set_={
                        "owner_telegram_user_id": record.owner_telegram_user_id,
                        "is_enabled": record.is_enabled,
                    },
                )
                .returning(BusinessConnectionModel)
            )
            row = (await sess.execute(stmt)).scalar_one()
            await sess.flush()
            return _to_dto(row)

    async def get(self, connection_id: str) -> BusinessConnectionDTO | None:
        async with self._session() as sess:
            stmt = select(BusinessConnectionModel).where(
                BusinessConnectionModel.connection_id == connection_id
            )
            row = (await sess.execute(stmt)).scalar_one_or_none()
            return _to_dto(row) if row else None

    async def set_enabled(self, connection_id: str, enabled: bool) -> None:
        async with self._session() as sess:
            stmt = select(BusinessConnectionModel).where(
                BusinessConnectionModel.connection_id == connection_id
            )
            row = (await sess.execute(stmt)).scalar_one_or_none()
            if row is not None:
                row.is_enabled = enabled

    async def delete(self, connection_id: str) -> None:
        async with self._session() as sess:
            await sess.execute(
                delete(BusinessConnectionModel).where(
                    BusinessConnectionModel.connection_id == connection_id
                )
            )
