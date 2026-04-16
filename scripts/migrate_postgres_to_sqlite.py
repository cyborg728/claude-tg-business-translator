"""Emergency reverse of the Phase-4.3 SQLite → Postgres copier.

Usage
-----
::

    python -m scripts.migrate_postgres_to_sqlite \\
        --source "$POSTGRES_DSN_SYNC" \\
        --target sqlite:///data/bot-rollback.db \\
        [--report backups/migration-<ts>.json] \\
        [--batch-size 1000]

When to reach for this
----------------------
This is the tier-2 rollback from MIGRATION_V2_TO_V3.md §4.4 — you've
cut over to Postgres, Postgres is observed to be broken, and the
tier-1 "flip config back to the pre-cutover SQLite snapshot" tier
would lose too many post-cutover writes. In that case:

1. Freeze writes on the Postgres side (scale workers to 0).
2. Run this script to build a **fresh** SQLite DB from the current
   Postgres contents.
3. Flip ``DATABASE_BACKEND=sqlite``, point `DATABASE_PATH` at the new
   file, restart workers.

The script is symmetric to ``migrate_sqlite_to_postgres.py``:

* Validates the URL pair (postgresql source, sqlite target).
* Runs ``alembic upgrade head`` against the SQLite target so the
  schema from `0001_initial_schema` exists.
* Preflights: refuses to run if any row-bearing table on the target
  already has rows — zero chance of clobbering an existing DB.
* Copies each table in FK-safe order with batched
  ``INSERT … ON CONFLICT DO NOTHING`` (SQLite dialect).
* Verifies row counts match per table; raises on mismatch.
* Writes a JSON report to ``--report`` (default
  ``backups/migration-<utc-ts>.json``).

What the script does **not** do (operator steps in the rollback
runbook): freeze writes, snapshot, flip ``DATABASE_BACKEND``, redirect
volume mounts.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Engine

logger = logging.getLogger("migrate_postgres_to_sqlite")

REPO_ROOT = Path(__file__).resolve().parent.parent
ALEMBIC_INI = REPO_ROOT / "alembic.ini"
ALEMBIC_SCRIPT_LOCATION = REPO_ROOT / "alembic"

# Same order as the forward script — keep the two in lock-step so that
# any future FK between tables stays honoured in both directions.
TABLES_IN_ORDER: tuple[str, ...] = (
    "users",
    "business_connections",
    "message_mappings",
    "kv_store",
)

# Columns that need a Postgres-native → SQLite-compatible Python type
# conversion on the way out.
UUID_COLUMN_NAMES = {"id"}

DEFAULT_BATCH_SIZE = 1000


# ── URL validators ──────────────────────────────────────────────────────────


def validate_postgres_url(url: str) -> None:
    """Reject anything that isn't a ``postgresql://`` source."""
    if not (url.startswith("postgresql://") or url.startswith("postgresql+")):
        raise ValueError(
            f"source must be a postgresql:// URL (optionally "
            f"postgresql+driver://…), got: {url!r}"
        )


def validate_sqlite_url(url: str) -> None:
    """Reject anything that isn't a ``sqlite://`` target."""
    if not url.startswith("sqlite://"):
        raise ValueError(f"target must be a sqlite:// URL, got: {url!r}")


# ── Row coercion ────────────────────────────────────────────────────────────


def coerce_row(row: Mapping[str, Any]) -> dict[str, Any]:
    """Normalise a Postgres row to SQLite-friendly Python types.

    * UUID columns: :class:`uuid.UUID` → hyphenated ``str`` (36 chars).
      SQLite stores ids as ``CHAR(36)`` via the historical v2 format —
      matches the ``UuidAsString36`` TypeDecorator on read-back.
    * Booleans pass through unchanged — SQLAlchemy's SQLite ``Boolean``
      type handles ``bool`` → ``0/1``.
    * Timestamps pass through unchanged — SQLAlchemy's SQLite
      ``DateTime`` accepts both tz-aware and naive datetimes.

    ``None`` and already-string ids pass through unchanged. Unknown
    columns are untouched.
    """
    out = dict(row)
    for key, value in out.items():
        if value is None:
            continue
        if key in UUID_COLUMN_NAMES and isinstance(value, uuid.UUID):
            out[key] = str(value)
    return out


# ── Alembic helpers ─────────────────────────────────────────────────────────


def run_alembic_upgrade_head(target_url: str) -> None:
    """Bring the target SQLite schema up to ``head`` (idempotent)."""
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("script_location", str(ALEMBIC_SCRIPT_LOCATION))
    cfg.set_main_option("sqlalchemy.url", target_url)
    command.upgrade(cfg, "head")


# ── Preflight ──────────────────────────────────────────────────────────────


def ensure_target_empty(target_engine: Engine) -> None:
    """Raise if any row-bearing table on the target already has rows."""
    with target_engine.connect() as conn:
        for table in TABLES_IN_ORDER:
            (count,) = conn.execute(
                sa.text(f"SELECT COUNT(*) FROM {table}")
            ).one()
            if count:
                raise RuntimeError(
                    f"refuse to migrate: target table {table!r} already "
                    f"contains {count} row(s); expected empty SQLite"
                )


# ── Copy per table ─────────────────────────────────────────────────────────


@dataclass
class TableReport:
    """Per-table rollback outcome captured in the JSON report."""

    source_rows: int = 0
    target_rows: int = 0
    duration_s: float = 0.0


def _batched(iterable: Iterable[Mapping[str, Any]], size: int) -> Iterable[list[dict[str, Any]]]:
    batch: list[dict[str, Any]] = []
    for row in iterable:
        batch.append(coerce_row(row))
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


def _count(conn: sa.Connection, table_name: str) -> int:
    (count,) = conn.execute(sa.text(f"SELECT COUNT(*) FROM {table_name}")).one()
    return int(count)


def copy_table(
    source_engine: Engine,
    target_engine: Engine,
    table_name: str,
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> TableReport:
    """Copy one table Postgres → SQLite with ``ON CONFLICT DO NOTHING``."""
    t0 = time.monotonic()

    source_meta = sa.MetaData()
    target_meta = sa.MetaData()
    source_meta.reflect(bind=source_engine, only=[table_name])
    target_meta.reflect(bind=target_engine, only=[table_name])
    source_table = source_meta.tables[table_name]
    target_table = target_meta.tables[table_name]

    with source_engine.connect() as src_conn:
        source_rows = _count(src_conn, table_name)
        result = src_conn.execute(sa.select(source_table)).mappings()
        with target_engine.begin() as tgt_conn:
            for batch in _batched(result, batch_size):
                stmt = sqlite_insert(target_table).values(batch).on_conflict_do_nothing()
                tgt_conn.execute(stmt)

    with target_engine.connect() as tgt_conn:
        target_rows = _count(tgt_conn, table_name)

    return TableReport(
        source_rows=source_rows,
        target_rows=target_rows,
        duration_s=round(time.monotonic() - t0, 3),
    )


# ── Verification ───────────────────────────────────────────────────────────


def verify_row_counts(report: dict[str, TableReport]) -> list[str]:
    """Return a list of mismatches; empty list = all tables match."""
    mismatches: list[str] = []
    for table, t in report.items():
        if t.source_rows != t.target_rows:
            mismatches.append(
                f"{table}: source={t.source_rows} target={t.target_rows}"
            )
    return mismatches


# ── Orchestrator ───────────────────────────────────────────────────────────


@dataclass
class MigrationReport:
    source_url: str
    target_url: str
    started_at: str
    finished_at: str = ""
    duration_s: float = 0.0
    batch_size: int = DEFAULT_BATCH_SIZE
    tables: dict[str, TableReport] = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps(
            {
                **{k: v for k, v in asdict(self).items() if k != "tables"},
                "tables": {k: asdict(v) for k, v in self.tables.items()},
            },
            indent=2,
            sort_keys=True,
        )


def migrate(
    source_url: str,
    target_url: str,
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> MigrationReport:
    """Perform a full Postgres → SQLite rollback and return the report."""
    validate_postgres_url(source_url)
    validate_sqlite_url(target_url)

    report = MigrationReport(
        source_url=_redact(source_url),
        target_url=_redact(target_url),
        started_at=_utc_iso_now(),
        batch_size=batch_size,
    )
    t0 = time.monotonic()

    logger.info("alembic upgrade head → %s", report.target_url)
    run_alembic_upgrade_head(target_url)

    source_engine = sa.create_engine(source_url, future=True)
    target_engine = sa.create_engine(target_url, future=True)
    try:
        logger.info("preflight: ensure target is empty")
        ensure_target_empty(target_engine)

        for table in TABLES_IN_ORDER:
            logger.info("copying table %s", table)
            tr = copy_table(
                source_engine, target_engine, table, batch_size=batch_size
            )
            logger.info(
                "  %s: source=%d target=%d duration=%.3fs",
                table, tr.source_rows, tr.target_rows, tr.duration_s,
            )
            report.tables[table] = tr

        mismatches = verify_row_counts(report.tables)
        if mismatches:
            raise RuntimeError(
                "row-count mismatch after rollback: " + "; ".join(mismatches)
            )
    finally:
        source_engine.dispose()
        target_engine.dispose()

    report.finished_at = _utc_iso_now()
    report.duration_s = round(time.monotonic() - t0, 3)
    return report


# ── CLI ────────────────────────────────────────────────────────────────────


def _redact(url: str) -> str:
    """Strip credentials from a URL for safe logging/reporting."""
    parsed = sa.engine.make_url(url)
    if parsed.password:
        parsed = parsed.set(password="***")
    return str(parsed)


def _utc_iso_now() -> str:
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _default_report_path() -> Path:
    ts = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return REPO_ROOT / "backups" / f"rollback-{ts}.json"


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="migrate_postgres_to_sqlite",
        description=(
            "Emergency reverse of the Phase-4.3 forward copier. Runs "
            "`alembic upgrade head` on the SQLite target, refuses "
            "non-empty targets, copies every table with ON CONFLICT DO "
            "NOTHING."
        ),
    )
    p.add_argument(
        "--source",
        required=True,
        help=(
            "SQLAlchemy postgresql:// URL for the source database; use "
            "postgresql+psycopg://… for explicit driver selection"
        ),
    )
    p.add_argument(
        "--target",
        required=True,
        help="SQLAlchemy sqlite:// URL for the destination database",
    )
    p.add_argument(
        "--report",
        type=Path,
        default=None,
        help="JSON report destination (default: backups/rollback-<ts>.json)",
    )
    p.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_BATCH_SIZE,
        help=f"rows per INSERT batch (default: {DEFAULT_BATCH_SIZE})",
    )
    p.add_argument(
        "--log-level",
        default="INFO",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    try:
        report = migrate(
            args.source, args.target, batch_size=args.batch_size
        )
    except Exception:  # noqa: BLE001
        logger.exception("rollback migration failed")
        return 2

    report_path = args.report or _default_report_path()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report.to_json())
    logger.info("report written to %s", report_path)
    logger.info("rollback OK in %.3fs", report.duration_s)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
