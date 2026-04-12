"""initial schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-04-12 00:00:00.000000
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0001_initial"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.String(length=36), primary_key=True),
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
        sa.Column("id", sa.String(length=36), primary_key=True),
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
        sa.Column("id", sa.String(length=36), primary_key=True),
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
        sa.Column("id", sa.String(length=36), primary_key=True),
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
    )
    op.create_index("ix_kv_store_owner_id", "kv_store", ["owner_id"])
    op.create_index("ix_kv_store_key", "kv_store", ["key"])


def downgrade() -> None:
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
