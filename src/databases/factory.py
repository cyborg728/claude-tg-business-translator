"""Database factory — picks the concrete backend based on settings."""

from __future__ import annotations

from src.config import Settings

from .interfaces import AbstractDatabase


def create_database(settings: Settings) -> AbstractDatabase:
    if settings.database_backend == "sqlite":
        from .sqlite import SqliteDatabase

        return SqliteDatabase(settings.database_url)

    raise RuntimeError(
        f"Unsupported DATABASE_BACKEND={settings.database_backend!r}. "
        "Implement a new subpackage under src/databases/ and register it here."
    )
