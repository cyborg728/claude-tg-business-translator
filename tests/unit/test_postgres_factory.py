"""Factory dispatches to the correct backend."""

from __future__ import annotations

import pytest

from src.config.settings import Settings
from src.databases.factory import create_database
from src.databases.postgres import PostgresDatabase
from src.databases.sqlite import SqliteDatabase


def _build(**overrides) -> Settings:
    base = {"telegram_bot_token": "12345:ABC"}
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def test_sqlite_factory_returns_sqlite_database():
    db = create_database(_build(database_backend="sqlite", database_path=":memory:"))
    assert isinstance(db, SqliteDatabase)


def test_postgres_factory_returns_postgres_database_without_connecting():
    # Constructing the PostgresDatabase must NOT open a connection — the
    # factory is called at import time in unit tests, so touching the network
    # would be a regression.
    db = create_database(
        _build(
            database_backend="postgres",
            postgres_dsn="postgresql://bot:bot@unreachable-host:5432/bot",
        )
    )
    assert isinstance(db, PostgresDatabase)
    # Engine is still None until connect() is awaited.
    assert db._engine is None  # type: ignore[attr-defined]


def test_unsupported_backend_raises():
    # Pydantic's Literal validator should reject unknown backends before the
    # factory is even reached — verify at the settings layer.
    with pytest.raises(Exception):
        _build(database_backend="mongo")
