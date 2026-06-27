"""Composite scoring — Phase 2.

Blends the deterministic rule gates with the LLM semantic fit into a single
composite score per job. Hard gates (work-auth veto, exclusions) override the
blend and force the composite to 0.

    composite = w_llm * llm_fit + w_auth * auth_fit + w_grade * grade_fit

Weights come from profile.scoring.composite_weights. A vetoed role keeps its
component scores for the audit trail but is marked vetoed and scored 0.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from agent.scoring.llm_fit import LLMFit, score_fit
from agent.scoring.rules import RuleResult, apply_rules

logger = logging.getLogger(__name__)


@dataclass
class ScoredJob:
    """A job plus everything the scorer produced. Carries the original job dict."""

    job: dict[str, Any]
    rule: RuleResult
    llm: LLMFit
    auth_fit: float
    grade_fit: float
    llm_fit: float
    composite: float
    rationale: str
    match_points: list[str] = field(default_factory=list)
    gaps: list[str] = field(default_factory=list)

    @property
    def job_id(self) -> Any:
        return self.job.get("id")

    def score_row(self) -> dict[str, Any]:
        """Shape for the `scores` table (see agent/scoring/persistence.py)."""
        return {
            "job_id": self.job_id,
            "rule_flags": {
                **self.rule.to_json(),
                "auth_flags": self.rule.flags,
            },
            "auth_fit": round(self.auth_fit, 2),
            "grade_fit": round(self.grade_fit, 2),
            "llm_fit": round(self.llm_fit, 2),
            "composite": round(self.composite, 2),
            "rationale": self.rationale,
            "match_points": self.match_points,
            "gaps": self.gaps,
        }


def _weights(profile: dict[str, Any]) -> tuple[float, float, float]:
    w = profile.get("scoring", {}).get("composite_weights", {})
    return (
        float(w.get("llm_fit", 0.60)),
        float(w.get("auth_fit", 0.20)),
        float(w.get("grade_fit", 0.20)),
    )


def composite_score(
    llm_fit: float, auth_fit: float, grade_fit: float, weights: tuple[float, float, float]
) -> float:
    """Weighted blend, normalised so weights need not sum to exactly 1.0."""
    w_llm, w_auth, w_grade = weights
    total = w_llm + w_auth + w_grade
    if total == 0:
        return 0.0
    return (w_llm * llm_fit + w_auth * auth_fit + w_grade * grade_fit) / total


def score_job(
    job: dict[str, Any],
    profile: dict[str, Any],
    *,
    client: Any | None = None,
) -> ScoredJob:
    """Score a single job: rule gates first, then LLM fit (skipped if vetoed)."""
    rule = apply_rules(job, profile)
    weights = _weights(profile)

    if rule.veto:
        # Don't spend an LLM call on a role that's already vetoed.
        llm = LLMFit(
            fit_score=0,
            match_points=[],
            gaps=[],
            rationale=f"Vetoed by rule gate ({rule.veto_reason}).",
        )
        return ScoredJob(
            job=job, rule=rule, llm=llm,
            auth_fit=rule.auth_fit, grade_fit=rule.grade_fit, llm_fit=0.0,
            composite=0.0,
            rationale=llm.rationale, match_points=[], gaps=[],
        )

    llm = score_fit(job, profile, client=client)
    composite = composite_score(
        float(llm.fit_score), rule.auth_fit, rule.grade_fit, weights
    )
    return ScoredJob(
        job=job, rule=rule, llm=llm,
        auth_fit=rule.auth_fit, grade_fit=rule.grade_fit, llm_fit=float(llm.fit_score),
        composite=composite,
        rationale=llm.rationale,
        match_points=llm.match_points,
        gaps=llm.gaps,
    )


def score_jobs(
    jobs: list[dict[str, Any]],
    profile: dict[str, Any],
    *,
    client: Any | None = None,
) -> list[ScoredJob]:
    """Score every job. One shared client so connections/keys are reused."""
    results: list[ScoredJob] = []
    for job in jobs:
        results.append(score_job(job, profile, client=client))
    logger.info("Scored %d jobs", len(results))
    return results
