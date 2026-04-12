from datetime import datetime

from sqlalchemy import DateTime, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Shared declarative base for all table models.

    ``eager_defaults=True`` tells SQLAlchemy to fetch server-generated column
    values (e.g. ``DEFAULT``, ``onupdate``) with a SELECT immediately after
    every INSERT/UPDATE, so the Python object is always up-to-date without an
    explicit ``session.refresh()``.
    """

    __mapper_args__ = {"eager_defaults": True}


class CreatedAtMixin:
    """Adds a ``created_at`` column set once on INSERT."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class TimestampMixin(CreatedAtMixin):
    """Adds ``created_at`` (INSERT) and ``updated_at`` (INSERT + UPDATE) columns."""

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
