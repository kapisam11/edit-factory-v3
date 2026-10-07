"""Backward-compatible import shim for the canonical dashboard migrations.

The production migration implementation lives in ``ai_video_factory.db_migrations``.
This module remains only for legacy callers that imported the old top-level name.
"""
from ai_video_factory.db_migrations import CURRENT_SCHEMA_VERSION, migrate


def apply_schema_migrations(conn):
    return migrate(conn)


def ensure_schema_migrations_table(conn):
    return migrate(conn)


__all__ = ["CURRENT_SCHEMA_VERSION", "migrate", "apply_schema_migrations", "ensure_schema_migrations_table"]
