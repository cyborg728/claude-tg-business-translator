"""Integration tests for ``scripts/migrate_postgres_to_sqlite.migrate``.

The reverse (tier-2 rollback) direction: Postgres → SQLite. The
live-Postgres tests are skipped by default and wake up when
``POSTGRES_TEST_URL`` is exported. CI runs them against a Postgres
service container.

The URL-validation tests run unconditionally — they fail fast before
any network connection is attempted.

The happy-path test uses the **forward** copier to populate Postgres
first, then runs the reverse copier into a fresh SQLite DB and checks
that every row round-trips.
"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa

from scripts.migrate_postgres_to_sqlite import (
    TABLES_IN_ORDER,
    main,
    migrate,
)
from scripts.migrate_sqlite_to_postgres import migrate as forward_migrate

REPO_ROOT = Path(__file__).resolve().parents[2]

POSTGRES_TEST_URL = os.environ.get("POSTGRES_TEST_URL")
pg_required = pytest.mark.skipif(
    not POSTGRES_TEST_URL,
    reason="set POSTGRES_TEST_URL to run live-Postgres rollback tests",
)


# ── URL validation surfaces before any network I/O ─────────────────────────


def test_migrate_rejects_non_postgres_source(tmp_path: Path):
    with pytest.raises(ValueError, match="postgresql"):
        migrate(
            f"sqlite:///{tmp_path / 'src.db'}",
            f"sqlite:///{tmp_path / 'tgt.db'}",
        )


def test_migrate_rejects_non_sqlite_target(tmp_path: Path):
    with pytest.raises(ValueError, match="sqlite://"):
        migrate("postgresql://bot@localhost/bot", "postgresql://bot@localhost/bot")


# ── Fixtures: populate PG via forward copier, fresh SQLite target ──────────


@pytest.fixture
def clean_postgres_url() -> str:
    """Drop our tables + alembic_version so every test starts blank."""
    url = POSTGRES_TEST_URL
    assert url, "POSTGRES_TEST_URL must be set when this fixture is used"
    engine = sa.create_engine(url, future=True)
    try:
        with engine.begin() as conn:
            for table in reversed(TABLES_IN_ORDER):
                conn.execute(sa.text(f"DROP TABLE IF EXISTS {table} CASCADE"))
            conn.execute(sa.text("DROP TABLE IF EXISTS alembic_version CASCADE"))
    finally:
        engine.dispose()
    return url


@pytest.fixture
def seeded_sqlite_source(tmp_path: Path) -> Path:
    """Build a small seeded SQLite so the forward migration has data."""
    db_file = tmp_path / "source.db"
    url = f"sqlite:///{db_file}"

    from alembic import command
    from alembic.config import Config

    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")

    engine = sa.create_engine(url, future=True)
    try:
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
    finally:
        engine.dispose()

    return db_file


def _sqlite_count(db_file: Path, table: str) -> int:
    eng = sa.create_engine(f"sqlite:///{db_file}", future=True)
    try:
        with eng.connect() as conn:
            (n,) = conn.execute(sa.text(f"SELECT COUNT(*) FROM {table}")).one()
            return int(n)
    finally:
        eng.dispose()


# ── Live Postgres: happy path + refusals + round-trip ──────────────────────


@pg_required
def test_rollback_happy_path_copies_every_row(
    seeded_sqlite_source: Path, clean_postgres_url: str, tmp_path: Path
):
    # Populate PG via forward copier so the reverse direction has data.
    forward_migrate(
        f"sqlite:///{seeded_sqlite_source}", clean_postgres_url
    )

    # Now roll back into a fresh SQLite target.
    target_db = tmp_path / "rollback.db"
    report = migrate(
        clean_postgres_url, f"sqlite:///{target_db}", batch_size=10
    )

    assert set(report.tables.keys()) == set(TABLES_IN_ORDER)
    for table, tr in report.tables.items():
        assert tr.source_rows > 0, table
        assert tr.source_rows == tr.target_rows, table
        assert _sqlite_count(target_db, table) == tr.source_rows

    # Totals match the seed (2 users, 2 bcs, 1 mapping, 2 kv).
    assert report.tables["users"].source_rows == 2
    assert report.tables["business_connections"].source_rows == 2
    assert report.tables["message_mappings"].source_rows == 1
    assert report.tables["kv_store"].source_rows == 2


@pg_required
def test_rollback_stores_uuid_as_char36_on_sqlite(
    seeded_sqlite_source: Path, clean_postgres_url: str, tmp_path: Path
):
    """The v2 on-disk format is CHAR(36) hyphenated strings — rollback
    must preserve that so a v2 codebase could read the DB back."""
    forward_migrate(
        f"sqlite:///{seeded_sqlite_source}", clean_postgres_url
    )

    target_db = tmp_path / "rollback.db"
    migrate(clean_postgres_url, f"sqlite:///{target_db}")

    # Bypass the UuidAsString36 TypeDecorator by reading raw text.
    eng = sa.create_engine(f"sqlite:///{target_db}", future=True)
    try:
        with eng.connect() as conn:
            rows = conn.execute(
                sa.text("SELECT CAST(id AS TEXT) FROM users")
            ).all()
            assert len(rows) == 2
            for (raw_id,) in rows:
                assert isinstance(raw_id, str)
                assert len(raw_id) == 36
                # Parseable back into a UUID — no corruption.
                uuid.UUID(raw_id)

            # Booleans land as 0/1 on SQLite.
            bcs = conn.execute(
                sa.text(
                    "SELECT connection_id, is_enabled FROM business_connections "
                    "ORDER BY connection_id"
                )
            ).all()
            assert bcs == [("conn-1", 1), ("conn-2", 0)]
    finally:
        eng.dispose()


@pg_required
def test_rollback_refuses_non_empty_target(
    seeded_sqlite_source: Path, clean_postgres_url: str, tmp_path: Path
):
    forward_migrate(
        f"sqlite:///{seeded_sqlite_source}", clean_postgres_url
    )

    target_db = tmp_path / "rollback.db"
    # First run populates the SQLite target.
    migrate(clean_postgres_url, f"sqlite:///{target_db}")

    # Second run must refuse — safety against accidental re-copy.
    with pytest.raises(RuntimeError, match="refuse to migrate"):
        migrate(clean_postgres_url, f"sqlite:///{target_db}")


@pg_required
def test_rollback_writes_report_json_via_main(
    seeded_sqlite_source: Path, clean_postgres_url: str, tmp_path: Path
):
    forward_migrate(
        f"sqlite:///{seeded_sqlite_source}", clean_postgres_url
    )

    target_db = tmp_path / "rollback.db"
    report_path = tmp_path / "report.json"
    rc = main([
        "--source", clean_postgres_url,
        "--target", f"sqlite:///{target_db}",
        "--report", str(report_path),
        "--batch-size", "50",
    ])
    assert rc == 0
    assert report_path.exists()

    report = json.loads(report_path.read_text())
    assert set(report["tables"].keys()) == set(TABLES_IN_ORDER)
    assert report["batch_size"] == 50
    # Credentials must not leak into the report.
    assert "***" in report["source_url"] or "@" not in report["source_url"]
