from datetime import datetime

from sqlalchemy import BigInteger, DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin


class UserRecord(TimestampMixin, Base):
    """Caches Telegram user info and their detected language."""

    __tablename__ = "users"

    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    first_name: Mapped[str] = mapped_column(String(255))
    last_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # ISO 639-1 code from Telegram profile or auto-detected via Gemini.
    language_code: Mapped[str | None] = mapped_column(String(10), nullable=True)

    @property
    def full_name(self) -> str:
        if self.last_name:
            return f"{self.first_name} {self.last_name}"
        return self.first_name

    def __repr__(self) -> str:
        return f"UserRecord(id={self.user_id}, name={self.first_name!r})"


class AllowedUser(Base):
    """Username whitelist per owner: only translate messages from users in this table.

    If the table has no entries for a given owner, translation applies to nobody.
    Composite PK: (owner_chat_id, username).
    """

    __tablename__ = "allowed_users"

    # The bot-chat ID of the owner who manages this whitelist.
    owner_chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    # Stored in lowercase, without the leading @.
    username: Mapped[str] = mapped_column(String(32), primary_key=True)
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    def __repr__(self) -> str:
        return f"AllowedUser(owner={self.owner_chat_id}, username={self.username!r})"


class AuthorizedUser(Base):
    """Users (other than the main owner) who are allowed to use the bot.

    The main owner manages this list via /translator → Access.
    Stored by username (lowercase, no @).
    """

    __tablename__ = "authorized_users"

    username: Mapped[str] = mapped_column(String(32), primary_key=True)
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    def __repr__(self) -> str:
        return f"AuthorizedUser(username={self.username!r})"
