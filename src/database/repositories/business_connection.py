from abc import abstractmethod

from sqlalchemy import select, update

from sqlmodel import col

from ..models import BusinessConnectionRecord
from .base import BaseRepository


class IBusinessConnectionRepository(BaseRepository[BusinessConnectionRecord]):
    """Interface for business-connection persistence."""

    @abstractmethod
    async def upsert(self, record: BusinessConnectionRecord) -> BusinessConnectionRecord:
        """Insert or update a business connection record."""
        ...

    @abstractmethod
    async def get(self, connection_id: str) -> BusinessConnectionRecord | None:
        """Return the record for the given connection ID, or None."""
        ...

    @abstractmethod
    async def set_enabled(self, connection_id: str, *, enabled: bool) -> None:
        """Enable or disable a business connection."""
        ...

    @abstractmethod
    async def list_active(self) -> list[BusinessConnectionRecord]:
        """Return all currently-enabled connections."""
        ...


class BusinessConnectionRepository(IBusinessConnectionRepository):
    """SQLite implementation of IBusinessConnectionRepository."""

    async def upsert(self, record: BusinessConnectionRecord) -> BusinessConnectionRecord:
        async with self._session() as sess:
            existing = await sess.get(BusinessConnectionRecord, record.connection_id)
            if existing is None:
                sess.add(record)
            else:
                existing.owner_user_id = record.owner_user_id
                existing.owner_chat_id = record.owner_chat_id
                existing.is_enabled = record.is_enabled
            await sess.flush()
            return record

    async def get(self, connection_id: str) -> BusinessConnectionRecord | None:
        async with self._session() as sess:
            return await sess.get(BusinessConnectionRecord, connection_id)

    async def set_enabled(self, connection_id: str, *, enabled: bool) -> None:
        async with self._session() as sess:
            await sess.execute(
                update(BusinessConnectionRecord)
                .where(col(BusinessConnectionRecord.connection_id) == connection_id)
                .values(is_enabled=enabled)
            )

    async def list_active(self) -> list[BusinessConnectionRecord]:
        async with self._session() as sess:
            result = await sess.execute(
                select(BusinessConnectionRecord).where(
                    BusinessConnectionRecord.is_enabled.is_(True)
                )
            )
            return list(result.scalars().all())
