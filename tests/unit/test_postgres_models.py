"""Postgres-backend ORM schema — verify dialect-native types compile.

We don't spin up a real Postgres in unit tests; instead we rely on
SQLAlchemy's dialect machinery to emit ``CREATE TABLE`` DDL using the
Postgres dialect and assert the column types we care about (UUID,
TIMESTAMPTZ, BOOLEAN) render correctly. This catches regressions where
someone accidentally imports the SQLite model base or drops the
``dialects.postgresql`` UUID type.
"""

from __future__ import annotations

from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from src.databases.postgres.models import (
    Base,
    BusinessConnectionModel,
    KvStoreModel,
    MessageMappingModel,
    UserModel,
)


def _ddl(model) -> str:
    return str(CreateTable(model.__table__).compile(dialect=postgresql.dialect()))


def test_users_primary_key_renders_as_native_uuid():
    ddl = _ddl(UserModel)
    assert "id UUID NOT NULL" in ddl
    assert "PRIMARY KEY (id)" in ddl


def test_users_timestamps_render_as_timestamptz():
    ddl = _ddl(UserModel)
    assert "created_at TIMESTAMP WITH TIME ZONE" in ddl
    assert "updated_at TIMESTAMP WITH TIME ZONE" in ddl


def test_users_tg_id_has_unique_constraint_by_name():
    ddl = _ddl(UserModel)
    assert "uq_users_tg_id" in ddl


def test_business_connections_is_enabled_is_boolean():
    ddl = _ddl(BusinessConnectionModel)
    assert "is_enabled BOOLEAN NOT NULL" in ddl


def test_message_mappings_has_no_updated_at():
    ddl = _ddl(MessageMappingModel)
    assert "created_at TIMESTAMP WITH TIME ZONE" in ddl
    assert "updated_at" not in ddl  # mapping is append-only


def test_kv_store_has_owner_key_unique_constraint():
    # The Postgres repository relies on this constraint for ON CONFLICT upsert.
    ddl = _ddl(KvStoreModel)
    assert "uq_kv_store_owner_key" in ddl


def test_all_tables_share_the_same_metadata():
    # Sanity: every Postgres model must bind to the Postgres Base, otherwise
    # Alembic autogenerate would miss it on migration generation.
    for model in (UserModel, BusinessConnectionModel, MessageMappingModel, KvStoreModel):
        assert model.metadata is Base.metadata
