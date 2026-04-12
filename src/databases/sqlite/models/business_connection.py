"""Business-connection table."""

from __future__ import annotations

from sqlalchemy import BigInteger, Boolean, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin, UuidV7PrimaryKeyMixin


class BusinessConnectionModel(UuidV7PrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "business_connections"
    __table_args__ = (
        UniqueConstraint("connection_id", name="uq_business_connections_conn_id"),
    )

    connection_id: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    owner_telegram_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"BusinessConnectionModel(id={self.id}, conn={self.connection_id!r}, "
            f"enabled={self.is_enabled})"
        )
