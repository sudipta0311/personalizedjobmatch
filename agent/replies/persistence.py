"""Reply-side persistence — Phase 4.

Resolves reply threads back to the digest that created them, enforces
idempotency (a Gmail message is processed at most once), and records command +
application-status changes.
"""

from __future__ import annotations

import logging
from typing import Any

from psycopg2.extras import Json

from agent.db.client import get_connection, get_cursor

logger = logging.getLogger(__name__)


def recent_digests(limit: int = 8) -> list[dict[str, Any]]:
    """Most-recent digests (id, thread_id, index_map) for the poller to scan."""
    with get_connection() as conn:
        with get_cursor(conn) as cur:
            cur.execute(
                """
                SELECT id, gmail_thread_id, gmail_message_id, index_map
                FROM digests
                WHERE gmail_thread_id IS NOT NULL
                ORDER BY sent_at DESC
                LIMIT %s
                """,
                (limit,),
            )
            return [dict(r) for r in cur.fetchall()]


def claim_command(
    digest_id: Any,
    gmail_message_id: str,
    raw_text: str,
    parsed: list[dict[str, Any]],
) -> str | None:
    """Insert a command row, returning its id — or None if already processed.

    The unique gmail_message_id is the idempotency gate. We return the command id
    when the reply is NEW, or when a prior attempt was claimed but never finished
    (processed_at IS NULL) — so a crash mid-processing is retried. We return None
    only when the reply was already fully processed.
    """
    with get_connection() as conn:
        with get_cursor(conn) as cur:
            cur.execute(
                """
                INSERT INTO commands
                    (digest_id, gmail_message_id, received_at, raw_text, parsed)
                VALUES (%s, %s, now(), %s, %s)
                ON CONFLICT (gmail_message_id) DO NOTHING
                RETURNING id
                """,
                (digest_id, gmail_message_id, raw_text, Json(parsed)),
            )
            row = cur.fetchone()
            if row:
                return str(row["id"])   # brand-new reply

            # Row already exists — reprocess only if a prior run didn't finish.
            cur.execute(
                "SELECT id, processed_at FROM commands WHERE gmail_message_id = %s",
                (gmail_message_id,),
            )
            existing = cur.fetchone()
    if existing and existing["processed_at"] is None:
        return str(existing["id"])
    return None


def mark_command_processed(command_id: str) -> None:
    with get_connection() as conn:
        with get_cursor(conn) as cur:
            cur.execute(
                "UPDATE commands SET processed_at = now() WHERE id = %s",
                (command_id,),
            )


def set_application_status(job_id: Any, status: str, note: str | None = None) -> None:
    """Upsert the applications row for a job to a new pipeline status."""
    with get_connection() as conn:
        with get_cursor(conn) as cur:
            cur.execute(
                """
                INSERT INTO applications (job_id, status, notes, updated_at)
                VALUES (%s, %s, %s, now())
                ON CONFLICT (job_id) DO UPDATE SET
                    status = EXCLUDED.status,
                    notes = COALESCE(EXCLUDED.notes, applications.notes),
                    updated_at = now()
                """,
                (job_id, status, note),
            )
            cur.execute(
                "INSERT INTO events (job_id, type, payload) VALUES (%s, %s, %s)",
                (job_id, status, Json({"note": note} if note else {})),
            )


def log_event(job_id: Any, event_type: str, payload: dict[str, Any]) -> None:
    with get_connection() as conn:
        with get_cursor(conn) as cur:
            cur.execute(
                "INSERT INTO events (job_id, type, payload) VALUES (%s, %s, %s)",
                (job_id, event_type, Json(payload)),
            )


def get_jobs(job_ids: list[Any]) -> dict[str, dict[str, Any]]:
    """Fetch full job rows by id (keyed by id::text) for tailoring."""
    if not job_ids:
        return {}
    with get_connection() as conn:
        with get_cursor(conn) as cur:
            cur.execute(
                """
                SELECT id::text AS id, source, company, title, location, country,
                       market_tag, jd_text, url
                FROM jobs WHERE id::text = ANY(%s)
                """,
                ([str(j) for j in job_ids],),
            )
            return {r["id"]: dict(r) for r in cur.fetchall()}


def set_application_prepared(
    job_id: Any, cv_path: str, letter_path: str, answers: dict[str, Any]
) -> None:
    """Mark an application 'prepared' and store the generated artifact paths."""
    with get_connection() as conn:
        with get_cursor(conn) as cur:
            cur.execute(
                """
                INSERT INTO applications
                    (job_id, status, cv_path, letter_path, answers, prepared_at, updated_at)
                VALUES (%s, 'prepared', %s, %s, %s, now(), now())
                ON CONFLICT (job_id) DO UPDATE SET
                    status = 'prepared',
                    cv_path = EXCLUDED.cv_path,
                    letter_path = EXCLUDED.letter_path,
                    answers = EXCLUDED.answers,
                    prepared_at = now(),
                    updated_at = now()
                """,
                (job_id, cv_path, letter_path, Json(answers)),
            )
            cur.execute(
                "INSERT INTO events (job_id, type, payload) VALUES (%s, 'prepared', %s)",
                (job_id, Json({"cv_path": cv_path, "letter_path": letter_path})),
            )


def set_application_warm(job_id: Any, linkedin_play: dict[str, Any]) -> None:
    """Mark an application 'warm_drafted' and store the LinkedIn play JSON."""
    with get_connection() as conn:
        with get_cursor(conn) as cur:
            cur.execute(
                """
                INSERT INTO applications
                    (job_id, status, linkedin_play, warm_drafted_at, updated_at)
                VALUES (%s, 'warm_drafted', %s, now(), now())
                ON CONFLICT (job_id) DO UPDATE SET
                    status = 'warm_drafted',
                    linkedin_play = EXCLUDED.linkedin_play,
                    warm_drafted_at = now(),
                    updated_at = now()
                """,
                (job_id, Json(linkedin_play)),
            )
            cur.execute(
                "INSERT INTO events (job_id, type, payload) VALUES (%s, 'warm_drafted', %s)",
                (job_id, Json({"search_string": linkedin_play.get("search_string")})),
            )


def job_titles(job_ids: list[Any]) -> dict[Any, str]:
    """Map job_id -> 'Title — Company' for friendlier acknowledgements."""
    if not job_ids:
        return {}
    with get_connection() as conn:
        with get_cursor(conn) as cur:
            # index_map stores job_ids as JSON strings; cast the uuid column to
            # text so the comparison works (uuid = text has no operator).
            cur.execute(
                "SELECT id::text AS id, title, company FROM jobs WHERE id::text = ANY(%s)",
                ([str(j) for j in job_ids],),
            )
            return {
                r["id"]: f"{r['title']} — {r['company']}" for r in cur.fetchall()
            }
