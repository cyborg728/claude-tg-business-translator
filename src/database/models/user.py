from datetime import datetime
from typing import Optional

from sqlalchemy import BigInteger, Column, DateTime, String, func

from sqlmodel import Field

from .base import Base, TimestampMixin


class UserRecord(TimestampMixin, Base, table=True):
    """Caches Telegram user info and their detected language."""

    __tablename__ = "users"

    user_id: int = Field(sa_column=Column(BigInteger, primary_key=True))
    username: Optional[str] = Field(default=None, sa_column=Column(String(255), nullable=True))
    first_name: str = Field(sa_column=Column(String(255), nullable=False))
    last_name: Optional[str] = Field(default=None, sa_column=Column(String(255), nullable=True))
    # ISO 639-1 code from Telegram profile or auto-detected via Gemini.
    language_code: Optional[str] = Field(
        default=None, sa_column=Column(String(10), nullable=True)
    )

    @property
    def full_name(self) -> str:
        if self.last_name:
            return f"{self.first_name} {self.last_name}"
        return self.first_name

    def __repr__(self) -> str:
        return f"UserRecord(id={self.user_id}, name={self.first_name!r})"


class AllowedUser(Base, table=True):
    """Username whitelist per owner: only translate messages from users in this table.

    If the table has no entries for a given owner, translation applies to nobody.
    Composite PK: (owner_chat_id, username).
    """

    __tablename__ = "allowed_users"

    # The bot-chat ID of the owner who manages this whitelist.
    owner_chat_id: int = Field(sa_column=Column(BigInteger, primary_key=True))
    # Stored in lowercase, without the leading @.
    username: str = Field(sa_column=Column(String(32), primary_key=True))
    added_at: Optional[datetime] = Field(
        default=None, sa_column=Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    )

    def __repr__(self) -> str:
        return f"AllowedUser(owner={self.owner_chat_id}, username={self.username!r})"


class AuthorizedUser(Base, table=True):
    """Users (other than the main owner) who are allowed to use the bot.

    The main owner manages this list via /translator → Access.
    Stored by username (lowercase, no @).
    """

    __tablename__ = "authorized_users"

    username: str = Field(sa_column=Column(String(32), primary_key=True))
    added_at: Optional[datetime] = Field(
        default=None, sa_column=Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    )

    def __repr__(self) -> str:
        return f"AuthorizedUser(username={self.username!r})"
