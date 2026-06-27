"""Persist scoring results to Neon — Phase 2.

Writes one `scores` row per job (upsert on the job_id unique index, so re-runs
update rather than duplicate), stamps the resolved `market_tag` back onto the
`jobs` row, and appends a `scored` event for the audit trail.
"""

from __future__ import annotations

import logging
from typing import Any

from psycopg2.extras import Json

from agent.db.client import get_connection, get_cursor
from agent.scoring.score import ScoredJob

logger = logging.getLogger(__name__)


def persist_scores(scored: list[ScoredJob]) -> int:
    """Upsert score rows, update jobs.market_tag, and log scored events.

    Returns the number of jobs persisted. Jobs without an id (e.g. not yet in
    the DB) are skipped — scoring still works in-memory for the digest.
    """
    rows = [s for s in scored if s.job_id is not None]
    if not rows:
        return 0

    with get_connection() as conn:
        with get_cursor(conn) as cur:
            for s in rows:
                r = s.score_row()
                cur.execute(
                    """
                    INSERT INTO scores
                        (job_id, rule_flags, auth_fit, grade_fit, llm_fit,
                         composite, rationale, match_points, gaps)
                    VALUES
                        (%(job_id)s, %(rule_flags)s, %(auth_fit)s, %(grade_fit)s,
                         %(llm_fit)s, %(composite)s, %(rationale)s,
                         %(match_points)s, %(gaps)s)
                    ON CONFLICT (job_id) DO UPDATE SET
                        scored_at    = now(),
                        rule_flags   = EXCLUDED.rule_flags,
                        auth_fit     = EXCLUDED.auth_fit,
                        grade_fit    = EXCLUDED.grade_fit,
                        llm_fit      = EXCLUDED.llm_fit,
                        composite    = EXCLUDED.composite,
                        rationale    = EXCLUDED.rationale,
                        match_points = EXCLUDED.match_points,
                        gaps         = EXCLUDED.gaps
                    """,
                    {
                        "job_id": s.job_id,
                        "rule_flags": Json(r["rule_flags"]),
                        "auth_fit": r["auth_fit"],
                        "grade_fit": r["grade_fit"],
                        "llm_fit": r["llm_fit"],
                        "composite": r["composite"],
                        "rationale": r["rationale"],
                        "match_points": Json(r["match_points"]),
                        "gaps": Json(r["gaps"]),
                    },
                )
                cur.execute(
                    "UPDATE jobs SET market_tag = %s WHERE id = %s",
                    (s.rule.market_tag, s.job_id),
                )
                cur.execute(
                    "INSERT INTO events (job_id, type, payload) VALUES (%s, %s, %s)",
                    (s.job_id, "scored", Json({"composite": r["composite"],
                                               "veto": s.rule.veto})),
                )

    logger.info("Persisted scores for %d jobs", len(rows))
    return len(rows)
