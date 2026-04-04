from datetime import datetime, timezone

from sqlalchemy import Column, DateTime
from sqlalchemy.orm import declared_attr
from sqlmodel import SQLModel


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(SQLModel):
    """Shared base for all table models.

    ``eager_defaults=True`` tells SQLAlchemy to fetch server-generated column
    values (e.g. ``DEFAULT``, ``onupdate``) with a SELECT immediately after
    every INSERT/UPDATE, so the Python object is always up-to-date without an
    explicit ``session.refresh()``.
    """

    __mapper_args__ = {"eager_defaults": True}


class CreatedAtMixin:
    """Adds a ``created_at`` column set once on INSERT."""

    @declared_attr
    def created_at(cls) -> Column:
        return Column(DateTime, default=_utcnow, nullable=False)


class TimestampMixin(CreatedAtMixin):
    """Adds ``created_at`` (INSERT) and ``updated_at`` (INSERT + UPDATE) columns."""

    @declared_attr
    def updated_at(cls) -> Column:
        return Column(DateTime, default=_utcnow, onupdate=_utcnow, nullable=False)
