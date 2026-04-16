"""Alembic migrations: dual-dialect dispatch (SQLite live + Postgres offline).

The SQLite block upgrades/downgrades a throwaway file DB end-to-end.
The Postgres block uses Alembic's ``--sql`` (offline) mode so we don't
need a running Postgres inside the test sandbox — we still exercise the
real migration functions and the real Postgres dialect compiler.

A final opt-in test runs the full upgrade/downgrade cycle against a live
Postgres when ``POSTGRES_TEST_URL`` is set (used in CI).
"""

from __future__ import annotations

import contextlib
import io
import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

from src.databases.postgres.models import Base as PostgresBase
from src.databases.sqlite.models import Base as SqliteBase

REPO_ROOT = Path(__file__).resolve().parents[2]


# ── SQLite: end-to-end upgrade/downgrade against a live SQLite file ─────────


@pytest.fixture
def alembic_cfg(tmp_path: Path) -> tuple[Config, str]:
    """Build an alembic Config that points at a throwaway SQLite file."""
    db_file = tmp_path / "alembic-test.db"
    url = f"sqlite:///{db_file}"

    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg, url


def _table_names(url: str) -> set[str]:
    eng = create_engine(url)
    try:
        return set(inspect(eng).get_table_names()) - {"alembic_version"}
    finally:
        eng.dispose()


def test_upgrade_head_creates_all_model_tables(alembic_cfg):
    cfg, url = alembic_cfg
    command.upgrade(cfg, "head")
    expected = set(SqliteBase.metadata.tables.keys())
    assert _table_names(url) == expected


def test_downgrade_base_drops_everything(alembic_cfg):
    cfg, url = alembic_cfg
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "base")
    assert _table_names(url) == set()


def test_upgrade_idempotent(alembic_cfg):
    cfg, url = alembic_cfg
    command.upgrade(cfg, "head")
    # Re-running upgrade head must not fail and must not change the schema.
    command.upgrade(cfg, "head")
    expected = set(SqliteBase.metadata.tables.keys())
    assert _table_names(url) == expected


def _capture_offline_sql(url: str, revision: str = "head", downgrade: bool = False) -> str:
    """Drive ``alembic upgrade|downgrade --sql`` against ``url`` and capture DDL.

    Offline mode writes to ``sys.stdout`` via ``env.py`` — we redirect it
    to an in-memory buffer so we can make assertions on the rendered SQL.
    """
    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        if downgrade:
            command.downgrade(cfg, revision, sql=True)
        else:
            command.upgrade(cfg, revision, sql=True)
    return buf.getvalue()


def test_sqlite_does_not_emit_postgres_native_types(tmp_path: Path):
    """0002_postgres_parity must be a no-op on SQLite.

    Uses Alembic's ``--sql`` mode so we see exactly what DDL the chain
    would emit and assert no UUID / TIMESTAMPTZ leak through the SQLite
    path.
    """
    sql = _capture_offline_sql(f"sqlite:///{tmp_path / 'offline.db'}")

    # SQLite-only shapes from 0001.
    assert "VARCHAR(36)" in sql
    # Nothing Postgres-native leaks in.
    assert " UUID" not in sql
    assert "TIMESTAMP WITH TIME ZONE" not in sql


# ── Postgres: offline SQL generation against the postgresql dialect ─────────


@pytest.fixture
def postgres_offline_sql() -> str:
    """Run ``alembic upgrade head --sql`` against the Postgres dialect.

    No live database is needed — SQLAlchemy parses the dummy URL solely
    to pick the dialect compiler.
    """
    return _capture_offline_sql("postgresql://dummy:dummy@localhost/dummy")


def test_postgres_upgrade_creates_all_four_tables(postgres_offline_sql):
    for table in PostgresBase.metadata.tables.keys():
        assert f"CREATE TABLE {table}" in postgres_offline_sql, table


def test_postgres_upgrade_uses_native_uuid_for_primary_keys(postgres_offline_sql):
    # Every ``id`` column must be UUID, not CHAR(36)/VARCHAR(36).
    assert postgres_offline_sql.count("id UUID NOT NULL") == 4
    assert "id VARCHAR(36)" not in postgres_offline_sql
    assert "id CHAR(36)" not in postgres_offline_sql


def test_postgres_upgrade_uses_timestamptz(postgres_offline_sql):
    assert "created_at TIMESTAMP WITH TIME ZONE" in postgres_offline_sql
    assert "updated_at TIMESTAMP WITH TIME ZONE" in postgres_offline_sql


def test_postgres_upgrade_emits_kv_store_owner_key_constraint(postgres_offline_sql):
    # Phase-4.1 Postgres kv-store repo relies on this constraint name.
    assert (
        "CONSTRAINT uq_kv_store_owner_key UNIQUE (owner_id, key)"
        in postgres_offline_sql
    )


def test_postgres_upgrade_records_both_revisions(postgres_offline_sql):
    # 0001 is a no-op body on Postgres but still gets recorded; 0002 follows.
    assert "'0001_initial'" in postgres_offline_sql
    assert "'0002_postgres_parity'" in postgres_offline_sql


def test_postgres_downgrade_drops_all_four_tables():
    # Walk the full chain down; exercises 0002.downgrade on Postgres.
    sql = _capture_offline_sql(
        "postgresql://dummy:dummy@localhost/dummy",
        revision="0002_postgres_parity:base",
        downgrade=True,
    )
    for table in PostgresBase.metadata.tables.keys():
        assert f"DROP TABLE {table}" in sql, table


# ── Optional: live Postgres (opt-in via $POSTGRES_TEST_URL) ─────────────────


@pytest.mark.skipif(
    not os.environ.get("POSTGRES_TEST_URL"),
    reason="set POSTGRES_TEST_URL to run live-Postgres alembic tests",
)
def test_live_postgres_upgrade_head_and_downgrade_base():
    """End-to-end upgrade/downgrade against a real Postgres (CI only).

    Use e.g. ``POSTGRES_TEST_URL=postgresql+psycopg://bot:bot@localhost:5432/bot_test``.
    """
    url = os.environ["POSTGRES_TEST_URL"]
    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)

    try:
        command.upgrade(cfg, "head")
        assert _table_names(url) == set(PostgresBase.metadata.tables.keys())

        command.upgrade(cfg, "head")  # idempotent
        assert _table_names(url) == set(PostgresBase.metadata.tables.keys())
    finally:
        command.downgrade(cfg, "base")

    assert _table_names(url) == set()
