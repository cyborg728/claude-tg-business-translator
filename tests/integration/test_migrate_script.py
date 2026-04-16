"""Integration tests for ``scripts/migrate_sqlite_to_postgres.migrate``.

The live-Postgres tests are skipped by default and wake up when
``POSTGRES_TEST_URL`` is exported (psycopg sync DSN, e.g.
``postgresql+psycopg://bot:bot@localhost:5432/bot_test``). CI runs them
against a Postgres service container.

The URL-validation tests run unconditionally — they fail fast before
any network connection is attempted.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from scripts.migrate_sqlite_to_postgres import (
    TABLES_IN_ORDER,
    main,
    migrate,
)

REPO_ROOT = Path(__file__).resolve().parents[2]

POSTGRES_TEST_URL = os.environ.get("POSTGRES_TEST_URL")
pg_required = pytest.mark.skipif(
    not POSTGRES_TEST_URL,
    reason="set POSTGRES_TEST_URL to run live-Postgres migration tests",
)


# ── URL validation surfaces before any network I/O ─────────────────────────


def test_migrate_rejects_non_sqlite_source(tmp_path: Path):
    with pytest.raises(ValueError, match="sqlite://"):
        migrate("postgresql://bot@localhost/bot", "postgresql://bot@localhost/bot")


def test_migrate_rejects_non_postgres_target(tmp_path: Path):
    src = tmp_path / "src.db"
    with pytest.raises(ValueError, match="postgresql"):
        migrate(f"sqlite:///{src}", f"sqlite:///{tmp_path / 'tgt.db'}")


# ── Fixtures: prepared SQLite source + clean Postgres target ───────────────


@pytest.fixture
def sqlite_source_url(tmp_path: Path) -> str:
    """Build a populated SQLite source DB using the real 0001 migration."""
    db_file = tmp_path / "source.db"
    url = f"sqlite:///{db_file}"

    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")

    engine = sa.create_engine(url, future=True)
    try:
        _seed_sqlite(engine)
    finally:
        engine.dispose()

    return url


def _seed_sqlite(engine: sa.Engine) -> None:
    """Insert a small, realistic fixture into the SQLite source."""
    meta = sa.MetaData()
    meta.reflect(engine)
    with engine.begin() as conn:
        conn.execute(meta.tables["users"].insert(), [
            {
                "id": str(uuid.uuid4()),
                "telegram_user_id": 111,
                "username": "alice",
                "first_name": "Alice",
                "last_name": None,
                "language_code": "en",
            },
            {
                "id": str(uuid.uuid4()),
                "telegram_user_id": 222,
                "username": None,
                "first_name": "Bob",
                "last_name": "Brown",
                "language_code": "ru",
            },
        ])
        conn.execute(meta.tables["business_connections"].insert(), [
            {
                "id": str(uuid.uuid4()),
                "connection_id": "conn-1",
                "owner_telegram_user_id": 111,
                "is_enabled": 1,
            },
            {
                "id": str(uuid.uuid4()),
                "connection_id": "conn-2",
                "owner_telegram_user_id": 222,
                "is_enabled": 0,
            },
        ])
        conn.execute(meta.tables["message_mappings"].insert(), [
            {
                "id": str(uuid.uuid4()),
                "business_connection_id": "conn-1",
                "user_telegram_id": 999,
                "user_chat_id": 999,
                "original_message_id": 1,
                "notification_message_id": 100,
                "original_text": "hello",
                "user_language": "en",
            },
        ])
        conn.execute(meta.tables["kv_store"].insert(), [
            {
                "id": str(uuid.uuid4()),
                "owner_id": 111,
                "key": "color",
                "value": "blue",
            },
            {
                "id": str(uuid.uuid4()),
                "owner_id": 111,
                "key": "lang",
                "value": "en",
            },
        ])


@pytest.fixture
def clean_postgres_url() -> str:
    """Bring the Postgres target to a known-empty state by dropping tables."""
    url = POSTGRES_TEST_URL
    assert url, "POSTGRES_TEST_URL must be set when this fixture is used"

    # Drop our tables + alembic_version so every test starts from a blank
    # schema. We intentionally *don't* DROP SCHEMA so the caller's
    # database / user / permissions survive.
    engine = sa.create_engine(url, future=True)
    try:
        with engine.begin() as conn:
            for table in reversed(TABLES_IN_ORDER):
                conn.execute(sa.text(f"DROP TABLE IF EXISTS {table} CASCADE"))
            conn.execute(sa.text("DROP TABLE IF EXISTS alembic_version CASCADE"))
    finally:
        engine.dispose()
    return url


def _pg_count(url: str, table: str) -> int:
    eng = sa.create_engine(url, future=True)
    try:
        with eng.connect() as conn:
            (n,) = conn.execute(sa.text(f"SELECT COUNT(*) FROM {table}")).one()
            return int(n)
    finally:
        eng.dispose()


# ── Live Postgres: happy path + refusals + resumability ────────────────────


@pg_required
def test_migrate_happy_path_copies_every_row(
    sqlite_source_url: str, clean_postgres_url: str
):
    report = migrate(sqlite_source_url, clean_postgres_url, batch_size=10)

    assert set(report.tables.keys()) == set(TABLES_IN_ORDER)
    for table, tr in report.tables.items():
        assert tr.source_rows > 0, table
        assert tr.source_rows == tr.target_rows, table
        assert _pg_count(clean_postgres_url, table) == tr.source_rows

    # Totals match the seed (2 users, 2 bcs, 1 mapping, 2 kv).
    assert report.tables["users"].source_rows == 2
    assert report.tables["business_connections"].source_rows == 2
    assert report.tables["message_mappings"].source_rows == 1
    assert report.tables["kv_store"].source_rows == 2


@pg_required
def test_migrate_preserves_uuid_and_tz_and_bool(
    sqlite_source_url: str, clean_postgres_url: str
):
    """Spot-check type normalisation: ids are real UUIDs, ts have tzinfo,
    is_enabled is bool on the Postgres side."""
    migrate(sqlite_source_url, clean_postgres_url)

    eng = sa.create_engine(clean_postgres_url, future=True)
    try:
        with eng.connect() as conn:
            users = conn.execute(sa.text("SELECT id, created_at FROM users")).all()
            assert len(users) == 2
            for uid, created_at in users:
                assert isinstance(uid, uuid.UUID)
                assert isinstance(created_at, datetime)
                assert created_at.tzinfo is not None

            bcs = conn.execute(
                sa.text("SELECT connection_id, is_enabled FROM business_connections ORDER BY connection_id")
            ).all()
            assert bcs == [("conn-1", True), ("conn-2", False)]
    finally:
        eng.dispose()


@pg_required
def test_migrate_refuses_non_empty_target(
    sqlite_source_url: str, clean_postgres_url: str
):
    # First run sets up + populates the target.
    migrate(sqlite_source_url, clean_postgres_url)

    # Re-running must refuse — safety against accidental re-copy.
    with pytest.raises(RuntimeError, match="refuse to migrate"):
        migrate(sqlite_source_url, clean_postgres_url)


@pg_required
def test_migrate_writes_report_json_via_main(
    sqlite_source_url: str, clean_postgres_url: str, tmp_path: Path
):
    report_path = tmp_path / "report.json"
    rc = main([
        "--source", sqlite_source_url,
        "--target", clean_postgres_url,
        "--report", str(report_path),
        "--batch-size", "50",
    ])
    assert rc == 0
    assert report_path.exists()

    import json

    report = json.loads(report_path.read_text())
    assert set(report["tables"].keys()) == set(TABLES_IN_ORDER)
    assert report["batch_size"] == 50
    # Credentials must not leak into the report.
    assert "***" in report["target_url"] or "@" not in report["target_url"]
