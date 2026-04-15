"""Postgres message-mapping repository (append-only, no upsert needed)."""

from __future__ import annotations

from sqlalchemy import select

from ...interfaces.message_mapping_repository import (
    IMessageMappingRepository,
    MessageMappingDTO,
)
from ..models import MessageMappingModel
from ._base import _PostgresRepositoryBase


def _to_dto(m: MessageMappingModel) -> MessageMappingDTO:
    return MessageMappingDTO(
        id=m.id,
        business_connection_id=m.business_connection_id,
        user_telegram_id=m.user_telegram_id,
        user_chat_id=m.user_chat_id,
        original_message_id=m.original_message_id,
        notification_message_id=m.notification_message_id,
        original_text=m.original_text,
        user_language=m.user_language,
        created_at=m.created_at,
    )


class PostgresMessageMappingRepository(_PostgresRepositoryBase, IMessageMappingRepository):
    async def add(self, mapping: MessageMappingDTO) -> MessageMappingDTO:
        async with self._session() as sess:
            model = MessageMappingModel(
                business_connection_id=mapping.business_connection_id,
                user_telegram_id=mapping.user_telegram_id,
                user_chat_id=mapping.user_chat_id,
                original_message_id=mapping.original_message_id,
                notification_message_id=mapping.notification_message_id,
                original_text=mapping.original_text,
                user_language=mapping.user_language,
            )
            sess.add(model)
            await sess.flush()
            return _to_dto(model)

    async def get_by_notification_id(
        self, notification_message_id: int
    ) -> MessageMappingDTO | None:
        async with self._session() as sess:
            stmt = select(MessageMappingModel).where(
                MessageMappingModel.notification_message_id == notification_message_id
            )
            row = (await sess.execute(stmt)).scalar_one_or_none()
            return _to_dto(row) if row else None
