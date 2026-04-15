"""Postgres-backend settings + driver rewriting."""

from __future__ import annotations

import pytest

from src.config.settings import Settings, _rewrite_postgres_driver


def _build(**overrides) -> Settings:
    base = {"telegram_bot_token": "12345:ABC"}
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


# ── driver rewriter ──────────────────────────────────────────────────────────

def test_rewrite_driver_accepts_bare_postgres_scheme():
    dsn = "postgres://u:p@h:5432/db"
    assert _rewrite_postgres_driver(dsn, "asyncpg") == (
        "postgresql+asyncpg://u:p@h:5432/db"
    )


def test_rewrite_driver_accepts_postgresql_scheme():
    dsn = "postgresql://u:p@h:5432/db"
    assert _rewrite_postgres_driver(dsn, "psycopg") == (
        "postgresql+psycopg://u:p@h:5432/db"
    )


def test_rewrite_driver_replaces_existing_driver_suffix():
    # If someone hard-codes +asyncpg and we ask for psycopg, the new driver wins.
    dsn = "postgresql+asyncpg://u:p@h/db"
    assert _rewrite_postgres_driver(dsn, "psycopg") == "postgresql+psycopg://u:p@h/db"


def test_rewrite_driver_preserves_query_string():
    dsn = "postgresql://u:p@h/db?sslmode=require&application_name=bot"
    assert _rewrite_postgres_driver(dsn, "asyncpg") == (
        "postgresql+asyncpg://u:p@h/db?sslmode=require&application_name=bot"
    )


def test_rewrite_driver_rejects_non_postgres_scheme():
    with pytest.raises(ValueError, match="postgres"):
        _rewrite_postgres_driver("mysql://u:p@h/db", "asyncpg")


def test_rewrite_driver_rejects_malformed_dsn():
    with pytest.raises(ValueError, match="not a URL"):
        _rewrite_postgres_driver("just-a-name", "asyncpg")


# ── Settings integration ─────────────────────────────────────────────────────

def test_postgres_backend_async_url_uses_asyncpg():
    s = _build(
        database_backend="postgres",
        postgres_dsn="postgresql://bot:bot@db:5432/bot",
    )
    assert s.database_url == "postgresql+asyncpg://bot:bot@db:5432/bot"


def test_postgres_backend_sync_url_uses_psycopg():
    s = _build(
        database_backend="postgres",
        postgres_dsn="postgresql://bot:bot@db:5432/bot",
    )
    assert s.database_url_sync == "postgresql+psycopg://bot:bot@db:5432/bot"


def test_postgres_backend_accepts_bare_postgres_scheme():
    # Many managed providers (Heroku, Render, …) still hand out ``postgres://``.
    s = _build(
        database_backend="postgres",
        postgres_dsn="postgres://bot:bot@db:5432/bot?sslmode=require",
    )
    assert s.database_url.startswith("postgresql+asyncpg://")
    assert "sslmode=require" in s.database_url


def test_backend_is_case_insensitive_for_postgres():
    s = _build(database_backend="POSTGRES", postgres_dsn="postgresql://u@h/d")
    assert s.database_backend == "postgres"


def test_sqlite_backend_still_works_unchanged():
    s = _build(database_backend="sqlite", database_path="data/test.db")
    assert s.database_url == "sqlite+aiosqlite:///data/test.db"
    assert s.database_url_sync == "sqlite:///data/test.db"
