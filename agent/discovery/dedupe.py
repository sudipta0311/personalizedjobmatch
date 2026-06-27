"""Deduplication — filters out job postings already stored in the DB.

Strategy: hash each posting on (company + normalised_title + location) and
skip anything whose content_hash already exists in the jobs table.
"""

from __future__ import annotations

import logging
from typing import Any

from agent.db.client import get_connection, get_cursor
from agent.discovery.base import JobPosting
from agent.utils.hashing import content_hash

logger = logging.getLogger(__name__)


def dedupe(postings: list[JobPosting]) -> list[JobPosting]:
    """Return only postings whose content_hash is not yet in the DB."""
    if not postings:
        return []

    hashes = {
        content_hash(p.company, p.title, p.location): p
        for p in postings
    }

    with get_connection() as conn:
        with get_cursor(conn) as cur:
            cur.execute(
                "SELECT content_hash FROM jobs WHERE content_hash = ANY(%s)",
                (list(hashes.keys()),),
            )
            existing = {row["content_hash"] for row in cur.fetchall()}

    new_postings = [p for h, p in hashes.items() if h not in existing]
    skipped = len(postings) - len(new_postings)
    if skipped:
        logger.info("Dedupe: skipped %d already-known postings", skipped)
    logger.info("Dedupe: %d new postings to score", len(new_postings))
    return new_postings


def persist_jobs(postings: list[JobPosting]) -> list[dict[str, Any]]:
    """Insert new postings into the jobs table; return the inserted rows with IDs."""
    if not postings:
        return []

    inserted: list[dict[str, Any]] = []

    with get_connection() as conn:
        with get_cursor(conn) as cur:
            for posting in postings:
                row = posting.to_db_dict()
                cur.execute(
                    """
                    INSERT INTO jobs
                        (source, url, company, title, location, country,
                         jd_text, posted_date, content_hash)
                    VALUES
                        (%(source)s, %(url)s, %(company)s, %(title)s,
                         %(location)s, %(country)s, %(jd_text)s,
                         %(posted_date)s, %(content_hash)s)
                    ON CONFLICT (content_hash) DO NOTHING
                    RETURNING id, source, company, title, location, country,
                              url, jd_text, posted_date, content_hash
                    """,
                    row,
                )
                result = cur.fetchone()
                if result:
                    inserted.append(dict(result))

    logger.info("Persisted %d new jobs to the database", len(inserted))
    return inserted
