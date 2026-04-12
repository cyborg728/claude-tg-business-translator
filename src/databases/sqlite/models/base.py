"""SQLAlchemy declarative base and shared mixins."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from src.utils import uuid7_str


class Base(DeclarativeBase):
    """Shared declarative base.

    ``eager_defaults=True`` makes SQLAlchemy fetch server-side default values
    (e.g. ``server_default=func.now()``) immediately after INSERT so attached
    objects always see the real DB values without a manual ``session.refresh``.
    """

    __mapper_args__ = {"eager_defaults": True}


class UuidV7PrimaryKeyMixin:
    """UUIDv7 primary-key column.

    Stored as a 36-char string so SQLite (and every other backend) can store
    it natively without extra type adapters. UUIDv7 values sort monotonically
    by creation time, giving B-tree-friendly primary keys.
    """

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=uuid7_str,
    )


class CreatedAtMixin:
    """Adds a ``created_at`` column set once on INSERT."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


class TimestampMixin(CreatedAtMixin):
    """Adds ``created_at`` and ``updated_at`` (onupdate=now) columns."""

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
