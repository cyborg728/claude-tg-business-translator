"""Postgres user repository — native ``INSERT ... ON CONFLICT`` upsert."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from ...interfaces.user_repository import IUserRepository, UserDTO
from ..models import UserModel
from ._base import _PostgresRepositoryBase


def _to_dto(m: UserModel) -> UserDTO:
    return UserDTO(
        id=m.id,
        telegram_user_id=m.telegram_user_id,
        username=m.username,
        first_name=m.first_name,
        last_name=m.last_name,
        language_code=m.language_code,
        created_at=m.created_at,
        updated_at=m.updated_at,
    )


class PostgresUserRepository(_PostgresRepositoryBase, IUserRepository):
    async def upsert(self, user: UserDTO) -> UserDTO:
        """Insert or update in a single round-trip via ``ON CONFLICT``.

        ``language_code`` is only overwritten when the caller supplies a
        non-NULL value — matches SQLite repo semantics so an update-path that
        doesn't know the locale can't wipe a previously-detected one.
        """
        async with self._session() as sess:
            update_values = {
                "username": user.username,
                "first_name": user.first_name,
                "last_name": user.last_name,
            }
            if user.language_code is not None:
                update_values["language_code"] = user.language_code

            stmt = (
                pg_insert(UserModel)
                .values(
                    telegram_user_id=user.telegram_user_id,
                    username=user.username,
                    first_name=user.first_name,
                    last_name=user.last_name,
                    language_code=user.language_code,
                )
                .on_conflict_do_update(
                    constraint="uq_users_tg_id",
                    set_=update_values,
                )
                .returning(UserModel)
            )
            result = await sess.execute(stmt)
            row = result.scalar_one()
            await sess.flush()
            return _to_dto(row)

    async def get_by_telegram_id(self, telegram_user_id: int) -> UserDTO | None:
        async with self._session() as sess:
            stmt = select(UserModel).where(UserModel.telegram_user_id == telegram_user_id)
            row = (await sess.execute(stmt)).scalar_one_or_none()
            return _to_dto(row) if row else None

    async def set_language(self, telegram_user_id: int, language_code: str) -> None:
        async with self._session() as sess:
            stmt = select(UserModel).where(UserModel.telegram_user_id == telegram_user_id)
            row = (await sess.execute(stmt)).scalar_one_or_none()
            if row is not None:
                row.language_code = language_code
