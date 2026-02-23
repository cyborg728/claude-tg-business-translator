from abc import abstractmethod

from sqlalchemy import select

from ..models import MessageMapping
from .base import BaseRepository


class IMessageMappingRepository(BaseRepository[MessageMapping]):
    """Interface for message-mapping persistence."""

    @abstractmethod
    async def save(self, mapping: MessageMapping) -> MessageMapping:
        """Persist a new message mapping and return it with the generated ID."""
        ...

    @abstractmethod
    async def get_by_notification_id(self, notification_message_id: int) -> MessageMapping | None:
        """Return the mapping whose notification_message_id matches, or None."""
        ...

    @abstractmethod
    async def get_by_id(self, mapping_id: int) -> MessageMapping | None:
        """Return a mapping by its primary key."""
        ...


class MessageMappingRepository(IMessageMappingRepository):
    """SQLite implementation of IMessageMappingRepository."""

    async def save(self, mapping: MessageMapping) -> MessageMapping:
        async with self._session() as sess:
            sess.add(mapping)
            await sess.flush()
            await sess.refresh(mapping)
            return mapping

    async def get_by_notification_id(self, notification_message_id: int) -> MessageMapping | None:
        async with self._session() as sess:
            result = await sess.execute(
                select(MessageMapping).where(
                    MessageMapping.notification_message_id == notification_message_id
                )
            )
            return result.scalar_one_or_none()

    async def get_by_id(self, mapping_id: int) -> MessageMapping | None:
        async with self._session() as sess:
            return await sess.get(MessageMapping, mapping_id)
