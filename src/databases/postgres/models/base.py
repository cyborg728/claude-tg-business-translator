"""SQLAlchemy declarative base and shared mixins — Postgres-native types.

Mirrors :mod:`src.databases.sqlite.models.base` one-for-one but swaps the
physical column types to their Postgres-native equivalents:

* ``id``          → ``UUID`` (not ``String(36)``)
* ``created_at``  → ``TIMESTAMPTZ`` (same SQLAlchemy spelling, real type server-side)
* ``Boolean``     → native ``BOOLEAN`` (no emulation)

DTO shapes stay identical — ``UuidV7PrimaryKeyMixin.id`` is still exposed as
``str`` via ``UUID(as_uuid=False)`` so the :class:`UserDTO` / other DTOs stay
dialect-agnostic.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from src.utils import uuid7_str


class Base(DeclarativeBase):
    """Shared declarative base for all Postgres ORM models."""

    __mapper_args__ = {"eager_defaults": True}


class UuidV7PrimaryKeyMixin:
    """UUIDv7 primary-key column backed by Postgres' native ``uuid`` type.

    ``as_uuid=False`` hands :class:`str` values back to the ORM, which keeps
    the cross-dialect DTOs identical to SQLite's ``String(36)`` representation.
    """

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        default=uuid7_str,
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
