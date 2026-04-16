"""Postgres parity (dual-dialect)

Revision ID: 0002_postgres_parity
Revises: 0001_initial
Create Date: 2026-04-16 00:00:00.000000

This revision is the Postgres half of the dual-dialect migration chain.

* On **SQLite** it is a no-op — the schema is already fully created by
  ``0001_initial``.
* On **Postgres** it creates the parallel schema using dialect-native
  types (``UUID``, ``TIMESTAMPTZ``, ``BOOLEAN``) plus the
  ``uq_kv_store_owner_key`` composite unique constraint that the
  Phase-4.1 Postgres kv-store repository relies on for
  ``INSERT … ON CONFLICT ON CONSTRAINT`` upserts.

Why this shape and not a single bidialectal ``0001``?
  * Keeps the committed v2 SQLite history untouched — production SQLite
    installs stay on the same ``CHAR(36)`` on-disk format they were
    shipped with.
  * Every subsequent migration can stay dialect-agnostic again —
    Postgres and SQLite will be structurally identical after this
    revision (columns match, only physical types differ).
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_postgres_parity"
down_revision: Union[str, Sequence[str], None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # SQLite schema was already created by 0001_initial.
    if op.get_bind().dialect.name != "postgresql":
        return

    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=False),
        sa.Column("username", sa.String(length=255), nullable=True),
        sa.Column("first_name", sa.String(length=255), nullable=False),
        sa.Column("last_name", sa.String(length=255), nullable=True),
        sa.Column("language_code", sa.String(length=10), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("telegram_user_id", name="uq_users_tg_id"),
    )
    op.create_index("ix_users_telegram_user_id", "users", ["telegram_user_id"])

    op.create_table(
        "business_connections",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("connection_id", sa.String(length=255), nullable=False),
        sa.Column("owner_telegram_user_id", sa.BigInteger(), nullable=False),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "connection_id", name="uq_business_connections_conn_id"
        ),
    )
    op.create_index(
        "ix_business_connections_connection_id",
        "business_connections",
        ["connection_id"],
    )

    op.create_table(
        "message_mappings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("business_connection_id", sa.String(length=255), nullable=False),
        sa.Column("user_telegram_id", sa.BigInteger(), nullable=False),
        sa.Column("user_chat_id", sa.BigInteger(), nullable=False),
        sa.Column("original_message_id", sa.Integer(), nullable=False),
        sa.Column("notification_message_id", sa.Integer(), nullable=False),
        sa.Column("original_text", sa.Text(), nullable=False),
        sa.Column("user_language", sa.String(length=10), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_message_mappings_notification_message_id",
        "message_mappings",
        ["notification_message_id"],
    )

    op.create_table(
        "kv_store",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("owner_id", sa.BigInteger(), nullable=False),
        sa.Column("key", sa.String(length=128), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        # Named constraint — Phase-4.1 kv-store repo does
        # ``on_conflict_do_update(constraint="uq_kv_store_owner_key", ...)``.
        sa.UniqueConstraint("owner_id", "key", name="uq_kv_store_owner_key"),
    )
    op.create_index("ix_kv_store_owner_id", "kv_store", ["owner_id"])
    op.create_index("ix_kv_store_key", "kv_store", ["key"])


def downgrade() -> None:
    # Mirrors upgrade(): SQLite schema is owned by 0001_initial.
    if op.get_bind().dialect.name != "postgresql":
        return

    op.drop_index("ix_kv_store_key", table_name="kv_store")
    op.drop_index("ix_kv_store_owner_id", table_name="kv_store")
    op.drop_table("kv_store")

    op.drop_index(
        "ix_message_mappings_notification_message_id", table_name="message_mappings"
    )
    op.drop_table("message_mappings")

    op.drop_index(
        "ix_business_connections_connection_id", table_name="business_connections"
    )
    op.drop_table("business_connections")

    op.drop_index("ix_users_telegram_user_id", table_name="users")
    op.drop_table("users")
