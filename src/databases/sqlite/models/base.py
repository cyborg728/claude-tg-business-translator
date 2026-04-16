"""SQLAlchemy declarative base and shared mixins.

UUIDs are exposed to Python as :class:`uuid.UUID` across every backend.
SQLite has no native UUID datatype, so we keep the existing
``CHAR(36)`` with-hyphens storage and bridge it to :class:`uuid.UUID`
via a :class:`TypeDecorator`. That preserves the historical on-disk
format (no Alembic data migration needed) while giving the ORM and
DTOs a typed identifier, identical to what the Postgres backend
returns.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator

from src.utils import uuid7


class UuidAsString36(TypeDecorator):
    """Stores ``uuid.UUID`` as a 36-char hyphenated string.

    Exists so the SQLite backend can keep its existing ``CHAR(36)``
    column while the Python layer always sees :class:`uuid.UUID`. Uses
    the same storage format the bot has been writing since v2 — no data
    migration required.
    """

    impl = String(36)
    cache_ok = True

    def process_bind_param(self, value, dialect):  # noqa: D401 - SA hook
        if value is None:
            return None
        if isinstance(value, uuid.UUID):
            return str(value)
        # Tolerate str input (legacy callers / raw SQL) by round-tripping.
        return str(uuid.UUID(value))

    def process_result_value(self, value, dialect):  # noqa: D401 - SA hook
        if value is None:
            return None
        if isinstance(value, uuid.UUID):
            return value
        return uuid.UUID(value)


class Base(DeclarativeBase):
    """Shared declarative base.

    ``eager_defaults=True`` makes SQLAlchemy fetch server-side default values
    (e.g. ``server_default=func.now()``) immediately after INSERT so attached
    objects always see the real DB values without a manual ``session.refresh``.
    """

    __mapper_args__ = {"eager_defaults": True}


class UuidV7PrimaryKeyMixin:
    """UUIDv7 primary-key column (:class:`uuid.UUID` in Python, CHAR(36) on disk)."""

    id: Mapped[uuid.UUID] = mapped_column(
        UuidAsString36(),
        primary_key=True,
        default=uuid7,
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
