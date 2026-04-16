"""Operational scripts (backups, data migrations, one-shot tooling).

Kept as a package so unit tests can import module-level helpers via
``from scripts.migrate_sqlite_to_postgres import coerce_row`` etc.
"""
