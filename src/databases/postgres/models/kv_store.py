"""Durable key/value per-owner store."""

from __future__ import annotations

from sqlalchemy import BigInteger, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin, UuidV7PrimaryKeyMixin


class KvStoreModel(UuidV7PrimaryKeyMixin, TimestampMixin, Base):
    """Per-owner key/value store with composite unique key (owner_id, key)."""

    __tablename__ = "kv_store"
    __table_args__ = (
        UniqueConstraint("owner_id", "key", name="uq_kv_store_owner_key"),
    )

    owner_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    key: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)
