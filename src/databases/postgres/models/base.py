"""SQLAlchemy declarative base and shared mixins — Postgres-native types.

Mirrors :mod:`src.databases.sqlite.models.base` one-for-one but swaps
the physical column types to their Postgres-native equivalents:

* ``id``          → native ``UUID`` (``as_uuid=True`` — Python-side
  :class:`uuid.UUID`, database-side ``uuid``)
* ``created_at``  → ``TIMESTAMPTZ`` (same SQLAlchemy spelling, native type)
* ``Boolean``     → native ``BOOLEAN`` (no emulation)
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from src.utils import uuid7


class Base(DeclarativeBase):
    """Shared declarative base for all Postgres ORM models."""

    __mapper_args__ = {"eager_defaults": True}


class UuidV7PrimaryKeyMixin:
    """UUIDv7 primary-key column backed by Postgres' native ``uuid`` type.

    ``as_uuid=True`` hands back :class:`uuid.UUID` objects — matches the
    SQLite backend (via :class:`UuidAsString36` TypeDecorator) so DTOs
    stay dialect-agnostic and typed.
    """

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid7,
    )


class CreatedAtMixin:
    """Adds a ``created_at`` column set once on INSERT (``TIMESTAMPTZ``)."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


class TimestampMixin(CreatedAtMixin):
    """Adds ``created_at`` and ``updated_at`` (``onupdate=now``) columns."""

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
