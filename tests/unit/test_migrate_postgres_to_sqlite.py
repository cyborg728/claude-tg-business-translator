"""Unit tests for ``scripts/migrate_postgres_to_sqlite.py`` helpers.

Mirror of :mod:`tests.unit.test_migrate_script`, inverted for the
reverse (tier-2 rollback) direction: source=postgres, target=sqlite.
The coercion is narrower on this side — only ``uuid.UUID`` → ``str``;
bool and datetime pass through untouched because SQLAlchemy's SQLite
``Boolean`` and ``DateTime`` types handle Python ``bool``/``datetime``
natively.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from scripts.migrate_postgres_to_sqlite import (
    DEFAULT_BATCH_SIZE,
    MigrationReport,
    TABLES_IN_ORDER,
    TableReport,
    _batched,
    _parse_args,
    _redact,
    coerce_row,
    validate_postgres_url,
    validate_sqlite_url,
    verify_row_counts,
)


# ── URL validators ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "url",
    [
        "postgresql://bot:pw@localhost/bot",
        "postgresql+psycopg://bot:pw@localhost/bot",
        "postgresql+asyncpg://bot@localhost/bot",
    ],
)
def test_validate_postgres_url_accepts(url):
    validate_postgres_url(url)  # no raise


@pytest.mark.parametrize(
    "url",
    [
        # "postgres://" (no trailing "ql") is intentionally rejected —
        # callers pass the normalised DSN from settings.database_url_sync.
        "postgres://bot@localhost/bot",
        "sqlite:///data/bot.db",
        "mysql://bot@localhost/bot",
        "",
    ],
)
def test_validate_postgres_url_rejects(url):
    with pytest.raises(ValueError, match="postgresql"):
        validate_postgres_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "sqlite:///data/bot.db",
        "sqlite:///:memory:",
        "sqlite:////absolute/path.db",
    ],
)
def test_validate_sqlite_url_accepts(url):
    validate_sqlite_url(url)  # no raise


@pytest.mark.parametrize(
    "url",
    [
        "postgresql://bot@localhost/bot",
        "postgres://bot@localhost/bot",
        "",
        "mysql://bot@localhost/bot",
    ],
)
def test_validate_sqlite_url_rejects(url):
    with pytest.raises(ValueError, match="sqlite://"):
        validate_sqlite_url(url)


# ── coerce_row ─────────────────────────────────────────────────────────────


def test_coerce_row_converts_uuid_to_string():
    uid = uuid.uuid4()
    row = {"id": uid, "first_name": "Alice"}
    out = coerce_row(row)
    assert out["id"] == str(uid)
    assert isinstance(out["id"], str)
    assert len(out["id"]) == 36  # hyphenated CHAR(36) — matches v2 format
    assert out["first_name"] == "Alice"


def test_coerce_row_passes_already_string_id_through():
    sid = str(uuid.uuid4())
    out = coerce_row({"id": sid})
    assert out["id"] == sid
    assert isinstance(out["id"], str)


def test_coerce_row_leaves_bool_untouched():
    # SQLite Boolean column accepts Python bool → 0/1 natively;
    # no coercion needed on the way in.
    assert coerce_row({"is_enabled": True})["is_enabled"] is True
    assert coerce_row({"is_enabled": False})["is_enabled"] is False


def test_coerce_row_leaves_datetime_untouched():
    # SQLAlchemy's SQLite DateTime type accepts both tz-aware and naive
    # datetimes; passing the Postgres TIMESTAMPTZ value through is fine.
    aware = datetime(2026, 4, 16, 12, 30, 0, tzinfo=timezone.utc)
    out = coerce_row({"created_at": aware})
    assert out["created_at"] is aware


def test_coerce_row_preserves_none_values():
    out = coerce_row({"id": None, "is_enabled": None, "created_at": None})
    assert out == {"id": None, "is_enabled": None, "created_at": None}


def test_coerce_row_leaves_unknown_columns_alone():
    row = {"foo": "bar", "count": 42, "blob": b"\x00"}
    assert coerce_row(row) == row


def test_coerce_row_does_not_mutate_input():
    row = {"id": uuid.uuid4(), "is_enabled": True}
    original = dict(row)
    coerce_row(row)
    assert row == original


# ── verify_row_counts ──────────────────────────────────────────────────────


def test_verify_row_counts_reports_no_mismatches_when_equal():
    report = {
        "users": TableReport(source_rows=10, target_rows=10),
        "business_connections": TableReport(source_rows=5, target_rows=5),
    }
    assert verify_row_counts(report) == []


def test_verify_row_counts_flags_short_target():
    report = {
        "users": TableReport(source_rows=10, target_rows=9),
        "kv_store": TableReport(source_rows=3, target_rows=3),
    }
    mismatches = verify_row_counts(report)
    assert len(mismatches) == 1
    assert "users" in mismatches[0]
    assert "source=10" in mismatches[0]
    assert "target=9" in mismatches[0]


# ── _batched ───────────────────────────────────────────────────────────────


def test_batched_splits_iterable_into_chunks():
    rows = [{"id": uuid.uuid4(), "n": i} for i in range(7)]
    batches = list(_batched(rows, 3))
    assert [len(b) for b in batches] == [3, 3, 1]
    # Every row gets coerced (UUID → str) on the way out.
    for batch in batches:
        for coerced in batch:
            assert isinstance(coerced["id"], str)


def test_batched_empty_iterable_yields_no_batches():
    assert list(_batched([], 100)) == []


# ── argparse ───────────────────────────────────────────────────────────────


def test_parse_args_requires_both_urls():
    with pytest.raises(SystemExit):
        _parse_args([])
    with pytest.raises(SystemExit):
        _parse_args(["--source", "postgresql://b@h/b"])


def test_parse_args_defaults():
    ns = _parse_args(
        ["--source", "postgresql://b@h/b", "--target", "sqlite:///x.db"]
    )
    assert ns.batch_size == DEFAULT_BATCH_SIZE
    assert ns.report is None
    assert ns.log_level == "INFO"


def test_parse_args_overrides():
    ns = _parse_args(
        [
            "--source", "postgresql+psycopg://b@h/b",
            "--target", "sqlite:///x.db",
            "--batch-size", "250",
            "--log-level", "DEBUG",
            "--report", "/tmp/out.json",
        ]
    )
    assert ns.batch_size == 250
    assert ns.log_level == "DEBUG"
    assert str(ns.report) == "/tmp/out.json"


# ── _redact ────────────────────────────────────────────────────────────────


def test_redact_strips_password_from_url():
    redacted = _redact("postgresql+psycopg://bot:supersecret@host:5432/db")
    assert "supersecret" not in redacted
    assert "***" in redacted
    assert "bot" in redacted  # username stays
    assert "host:5432" in redacted


def test_redact_leaves_password_less_url_alone():
    url = "sqlite:///data/bot-rollback.db"
    assert _redact(url) == url


# ── MigrationReport.to_json ────────────────────────────────────────────────


def test_migration_report_to_json_round_trips():
    import json

    r = MigrationReport(
        source_url="postgresql://b@h/b",
        target_url="sqlite:///x.db",
        started_at="2026-04-16T12:00:00Z",
        finished_at="2026-04-16T12:01:30Z",
        duration_s=90.5,
        batch_size=500,
    )
    r.tables["users"] = TableReport(source_rows=3, target_rows=3, duration_s=1.2)

    parsed = json.loads(r.to_json())
    assert parsed["source_url"] == "postgresql://b@h/b"
    assert parsed["target_url"] == "sqlite:///x.db"
    assert parsed["batch_size"] == 500
    assert parsed["tables"]["users"] == {
        "source_rows": 3, "target_rows": 3, "duration_s": 1.2,
    }


# ── TABLES_IN_ORDER sanity ─────────────────────────────────────────────────


def test_tables_in_order_matches_model_metadata():
    """Catches accidental drift (added/removed tables) — SQLite metadata
    because that's the target schema on rollback."""
    from src.databases.sqlite.models import Base

    assert set(TABLES_IN_ORDER) == set(Base.metadata.tables.keys())


def test_tables_in_order_matches_forward_script():
    """Forward/reverse must stay in lock-step on FK-safe ordering."""
    from scripts.migrate_sqlite_to_postgres import (
        TABLES_IN_ORDER as FORWARD_ORDER,
    )

    assert TABLES_IN_ORDER == FORWARD_ORDER
