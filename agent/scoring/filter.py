"""Filter scored jobs down to the digest shortlist — Phase 2.

Drops vetoed roles and anything below the composite cutoff, then keeps the
top-N by composite score for the weekly digest. Cutoff and top-N come from
profile.scoring (top-N can be overridden at runtime via DIGEST_TOP_N).
"""

from __future__ import annotations

import logging
from typing import Any

from agent.scoring.score import ScoredJob

logger = logging.getLogger(__name__)


def filter_jobs(
    scored: list[ScoredJob],
    profile: dict[str, Any],
    *,
    top_n_override: int | None = None,
) -> list[ScoredJob]:
    """Return the shortlist: non-vetoed, >= cutoff, top-N by composite (desc)."""
    scoring = profile.get("scoring", {})
    cutoff = float(scoring.get("cutoff_score", 60))
    top_n = top_n_override or int(scoring.get("top_n_digest", 10))

    eligible = [
        s for s in scored
        if not s.rule.veto and s.composite >= cutoff
    ]
    eligible.sort(key=lambda s: s.composite, reverse=True)
    shortlist = eligible[:top_n]

    vetoed = sum(1 for s in scored if s.rule.veto)
    below = len(scored) - vetoed - len(eligible)
    logger.info(
        "Filter: %d scored -> %d vetoed, %d below cutoff %.0f, %d shortlisted (top-%d)",
        len(scored), vetoed, below, cutoff, len(shortlist), top_n,
    )
    return shortlist
