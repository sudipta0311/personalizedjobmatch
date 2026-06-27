"""Persist sent-digest records to Neon — Phase 3.

One `digests` row per email sent: the run id, the Gmail thread/message ids, and
the index_map. The Phase 4 reply poller reads this row to resolve reply indices
(e.g. "prepare 1") back to job_ids and to know which thread to watch.
"""

from __future__ import annotations

import datetime as _dt
import logging
import os
from typing import Any

from psycopg2.extras import Json

from agent.db.client import get_connection, get_cursor

logger = logging.getLogger(__name__)


def current_run_id() -> str:
    """GitHub Actions run id when available, else a manual timestamp label."""
    return os.environ.get("GITHUB_RUN_ID") or (
        "manual-" + _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    )


def record_digest(
    run_id: str,
    gmail_thread_id: str,
    gmail_message_id: str,
    index_map: dict[str, Any],
) -> str:
    """Insert a digests row; return its id."""
    with get_connection() as conn:
        with get_cursor(conn) as cur:
            cur.execute(
                """
                INSERT INTO digests
                    (run_id, gmail_thread_id, gmail_message_id, index_map)
                VALUES (%s, %s, %s, %s)
                RETURNING id
                """,
                (run_id, gmail_thread_id, gmail_message_id, Json(index_map)),
            )
            row = cur.fetchone()

    digest_id = str(row["id"]) if row else ""
    logger.info("Recorded digest %s (run_id=%s, %d roles)",
                digest_id, run_id, len(index_map))
    return digest_id
