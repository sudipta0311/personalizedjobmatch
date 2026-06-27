"""Neon (PostgreSQL) connection management.

All agent code should obtain connections via get_connection() or run_in_transaction().
Never import psycopg2 directly elsewhere — keeps the DB backend swappable.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Generator

import psycopg2
import psycopg2.extras
from psycopg2.extensions import connection as PGConnection

_DATABASE_URL: str | None = None


def _database_url() -> str:
    global _DATABASE_URL
    if _DATABASE_URL is None:
        url = os.environ.get("NEON_DATABASE_URL")
        if not url:
            raise RuntimeError(
                "NEON_DATABASE_URL environment variable is not set. "
                "Copy .env.example to .env and fill it in, or set the GitHub Secret."
            )
        _DATABASE_URL = url
    return _DATABASE_URL


@contextmanager
def get_connection() -> Generator[PGConnection, None, None]:
    """Yield a psycopg2 connection; commit on clean exit, rollback on exception."""
    conn = psycopg2.connect(
        _database_url(),
        cursor_factory=psycopg2.extras.RealDictCursor,
    )
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@contextmanager
def get_cursor(conn: PGConnection):
    """Yield a cursor from an existing connection."""
    cur = conn.cursor()
    try:
        yield cur
    finally:
        cur.close()


def apply_schema(schema_path: str | None = None) -> None:
    """Run schema.sql against the database (idempotent — uses IF NOT EXISTS)."""
    import pathlib

    if schema_path is None:
        schema_path = str(
            pathlib.Path(__file__).parent / "schema.sql"
        )

    ddl = pathlib.Path(schema_path).read_text()
    with get_connection() as conn:
        with get_cursor(conn) as cur:
            cur.execute(ddl)
