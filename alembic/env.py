"""Alembic environment using sync driver for migrations.

Dual-dialect support
--------------------
Phase 4.2 splits the schema creation across two revisions so a single
``alembic upgrade head`` works against both backends:

* ``0001_initial``         — SQLite schema (CHAR(36) / Boolean / DateTime).
* ``0002_postgres_parity`` — Postgres-native schema (UUID / TIMESTAMPTZ /
  BOOLEAN) + ``uq_kv_store_owner_key``.

Each revision is a no-op on the "other" dialect. For autogenerate to work
against the active backend we load the matching declarative ``Base`` and
we only enable SQLite's ``render_as_batch`` workaround when the bind is
actually SQLite.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool
from sqlalchemy.engine.url import make_url

from src.config import get_settings

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Honor an explicit URL set on the Config (e.g. by tests, or
# `alembic -x url=… upgrade head`); otherwise default to the app settings.
if not config.get_main_option("sqlalchemy.url"):
    config.set_main_option("sqlalchemy.url", get_settings().database_url_sync)


def _dialect_name_from_url(url: str) -> str:
    """Extract the dialect name (``sqlite`` / ``postgresql``) from a URL."""
    return make_url(url).get_backend_name()


def _load_target_metadata(dialect: str):
    """Pick the declarative ``Base`` matching the active dialect.

    Autogenerate compares metadata against the live schema, so using the
    dialect-native models keeps ``compare_type=True`` accurate (e.g.
    Postgres sees ``UUID`` not ``CHAR(36)``).
    """
    if dialect == "postgresql":
        from src.databases.postgres.models import Base as PostgresBase

        return PostgresBase.metadata
    from src.databases.sqlite.models import Base as SqliteBase

    return SqliteBase.metadata


_url = config.get_main_option("sqlalchemy.url") or ""
_dialect = _dialect_name_from_url(_url)
_is_sqlite = _dialect == "sqlite"
target_metadata = _load_target_metadata(_dialect)


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        # SQLite-only ALTER TABLE workaround; Postgres does ALTER natively.
        render_as_batch=_is_sqlite,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section) or {},
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            render_as_batch=_is_sqlite,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
