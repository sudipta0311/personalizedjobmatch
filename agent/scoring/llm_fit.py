"""LLM semantic-fit scoring — Phase 2.

Given the user's profile and a single job description, ask Claude for a STRUCTURED
judgment: a 0-100 fit score, the strongest match points, the gaps, and a one-line
rationale. We use the Anthropic Python SDK's structured-output support
(`messages.parse` with a Pydantic schema) so the response is always valid JSON
that maps onto `LLMFit`.

Model is configurable per node (profile.yaml `models.scoring`, default Sonnet 4.6 —
high volume, very capable). The client is injectable so tests never hit the network.
"""

from __future__ import annotations

import logging
from typing import Any, Protocol

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

DEFAULT_SCORING_MODEL = "claude-sonnet-4-6"
_MAX_JD_CHARS = 12_000   # keep the prompt bounded; JDs past this are rarely more informative


class LLMFit(BaseModel):
    """Structured fit judgment returned by Claude."""

    fit_score: int = Field(description="Overall fit, 0-100, of this candidate for this role.")
    match_points: list[str] = Field(
        description="The 3 strongest, specific reasons this candidate fits."
    )
    gaps: list[str] = Field(
        description="1-2 honest gaps or weaknesses for this role."
    )
    rationale: str = Field(description="One-line overall rationale.")


class _ParseClient(Protocol):
    """Minimal structural type for the bit of the Anthropic SDK we use."""

    class messages:  # noqa: N801 - mirrors the SDK attribute name
        @staticmethod
        def parse(**kwargs: Any) -> Any: ...


def _build_client() -> Any:
    """Lazily construct an Anthropic client (reads ANTHROPIC_API_KEY from env)."""
    import anthropic

    return anthropic.Anthropic()


def _system_prompt(profile: dict[str, Any]) -> str:
    personal = profile.get("personal", {})
    cv = profile.get("cv_content", {})
    seniority = profile.get("seniority", {})
    return (
        "You are a precise technical recruiter scoring how well a senior candidate "
        "fits a specific role. Be honest and calibrated — most roles are a partial "
        "fit. Never invent experience the candidate doesn't have.\n\n"
        f"CANDIDATE: {personal.get('name')} — "
        f"{seniority.get('level')} level, {seniority.get('years_experience')} years.\n"
        f"SUMMARY: {cv.get('summary', '').strip()}\n"
        f"SKILLS: {_flatten_skills(cv.get('skills', {}))}\n"
        f"CERTIFICATIONS: {_format_certs(cv.get('certifications', []))}\n"
    )


def _flatten_skills(skills: dict[str, list[str]]) -> str:
    out: list[str] = []
    for group in skills.values():
        out.extend(group)
    return ", ".join(out)


def _format_certs(certs: list[dict[str, Any]]) -> str:
    return ", ".join(c.get("name", "") for c in certs)


def _user_prompt(job: dict[str, Any]) -> str:
    jd = (job.get("jd_text") or "")[:_MAX_JD_CHARS]
    return (
        f"ROLE: {job.get('title')} at {job.get('company')} "
        f"({job.get('location') or 'location n/a'})\n\n"
        f"JOB DESCRIPTION:\n{jd}\n\n"
        "Score this candidate against this role. Return: fit_score (0-100), "
        "exactly 3 match_points, 1-2 gaps, and a one-line rationale."
    )


def score_fit(
    job: dict[str, Any],
    profile: dict[str, Any],
    *,
    client: Any | None = None,
    model: str | None = None,
) -> LLMFit:
    """Return Claude's structured fit judgment for one job.

    Falls back to a neutral mid-score if the API call fails, so one bad role
    never aborts a whole discovery run.
    """
    model = model or profile.get("models", {}).get("scoring", DEFAULT_SCORING_MODEL)
    client = client or _build_client()

    try:
        response = client.messages.parse(
            model=model,
            max_tokens=1024,
            system=_system_prompt(profile),
            messages=[{"role": "user", "content": _user_prompt(job)}],
            output_format=LLMFit,
        )
        return response.parsed_output
    except Exception as exc:  # noqa: BLE001 - degrade gracefully, never crash the run
        logger.error(
            "LLM fit scoring failed for %r at %r: %s",
            job.get("title"), job.get("company"), exc,
        )
        return LLMFit(
            fit_score=50,
            match_points=[],
            gaps=["LLM scoring unavailable — review manually"],
            rationale="LLM scoring failed; neutral fallback score applied.",
        )
