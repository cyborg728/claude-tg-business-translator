"""SQLite user repository."""

from __future__ import annotations

from sqlalchemy import select

from ...interfaces.user_repository import IUserRepository, UserDTO
from ..models import UserModel
from ._base import _SqliteRepositoryBase


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


class SqliteUserRepository(_SqliteRepositoryBase, IUserRepository):
    async def upsert(self, user: UserDTO) -> UserDTO:
        async with self._session() as sess:
            stmt = select(UserModel).where(
                UserModel.telegram_user_id == user.telegram_user_id
            )
            existing = (await sess.execute(stmt)).scalar_one_or_none()

            if existing is None:
                model = UserModel(
                    telegram_user_id=user.telegram_user_id,
                    username=user.username,
                    first_name=user.first_name,
                    last_name=user.last_name,
                    language_code=user.language_code,
                )
                sess.add(model)
                await sess.flush()
                return _to_dto(model)

            existing.username = user.username
            existing.first_name = user.first_name
            existing.last_name = user.last_name
            if user.language_code is not None:
                existing.language_code = user.language_code
            await sess.flush()
            return _to_dto(existing)

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
