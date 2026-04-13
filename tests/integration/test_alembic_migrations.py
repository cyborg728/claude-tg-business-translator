"""Alembic migrations: upgrade head + downgrade base, schema matches metadata."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

from src.databases.sqlite.models import Base

REPO_ROOT = Path(__file__).resolve().parents[2]


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
    expected = set(Base.metadata.tables.keys())
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
    expected = set(Base.metadata.tables.keys())
    assert _table_names(url) == expected
