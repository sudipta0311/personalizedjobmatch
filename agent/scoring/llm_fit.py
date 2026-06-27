"""LLM semantic-fit scoring — Phase 2 (provider-agnostic).

Given the user's profile and a single job description, ask the configured LLM for
a STRUCTURED judgment: a 0-100 fit score, the strongest match points, the gaps,
and a one-line rationale.

Provider + model are configurable per node (`profile.yaml` `models.scoring`).
OpenAI is the default; Anthropic is selected with `provider: anthropic`. The
provider plumbing lives in agent.llm.client; here we just build the prompt and
validate into `LLMFit`. The client is injectable so tests never hit the network.
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel, Field

from agent.llm.client import parse_structured, resolve_node

logger = logging.getLogger(__name__)

_MAX_JD_CHARS = 12_000   # keep the prompt bounded; JDs past this are rarely more informative


class LLMFit(BaseModel):
    """Structured fit judgment returned by the LLM."""

    fit_score: int = Field(description="Overall fit, 0-100, of this candidate for this role.")
    match_points: list[str] = Field(
        description="The 3 strongest, specific reasons this candidate fits."
    )
    gaps: list[str] = Field(
        description="1-2 honest gaps or weaknesses for this role."
    )
    rationale: str = Field(description="One-line overall rationale.")


def _system_prompt(profile: dict[str, Any]) -> str:
    personal = profile.get("personal", {})
    cv = profile.get("cv_content", {})
    seniority = profile.get("seniority", {})
    prefs = (profile.get("job_preferences") or "").strip()
    prefs_block = (
        f"CANDIDATE PREFERENCES (weigh these in the score — roles that match the "
        f"preferred role types, locations, and visa situation should score higher; "
        f"roles outside them lower):\n{prefs}\n\n"
        if prefs else ""
    )
    return (
        "You are a precise technical recruiter scoring how well a senior candidate "
        "fits a specific role. Be honest and calibrated — most roles are a partial "
        "fit. Never invent experience the candidate doesn't have.\n\n"
        f"CANDIDATE: {personal.get('name')} — "
        f"{seniority.get('level')} level, {seniority.get('years_experience')} years.\n"
        f"SUMMARY: {(cv.get('summary') or '').strip()}\n"
        f"SKILLS: {_flatten_skills(cv.get('skills', {}))}\n"
        f"CERTIFICATIONS: {_format_certs(cv.get('certifications', []))}\n\n"
        f"{prefs_block}"
        "Respond with ONLY a JSON object with exactly these keys:\n"
        '  "fit_score": integer 0-100,\n'
        '  "match_points": array of exactly 3 short strings,\n'
        '  "gaps": array of 1-2 short strings,\n'
        '  "rationale": one-line string.\n'
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
        "Score this candidate against this role and return the JSON object."
    )


def score_fit(
    job: dict[str, Any],
    profile: dict[str, Any],
    *,
    client: Any | None = None,
    provider: str | None = None,
    model: str | None = None,
) -> LLMFit:
    """Return the LLM's structured fit judgment for one job.

    Falls back to a neutral mid-score if the call fails, so one bad role never
    aborts a whole discovery run.
    """
    node_provider, node_model = resolve_node(profile, "scoring")
    provider = provider or node_provider
    model = model or node_model

    try:
        return parse_structured(
            provider=provider,
            model=model,
            system=_system_prompt(profile),
            user=_user_prompt(job),
            schema_model=LLMFit,
            client=client,
        )
    except Exception as exc:  # noqa: BLE001 - degrade gracefully, never crash the run
        logger.error(
            "LLM fit scoring failed (%s/%s) for %r at %r: %s",
            provider, model, job.get("title"), job.get("company"), exc,
        )
        return LLMFit(
            fit_score=50,
            match_points=[],
            gaps=["LLM scoring unavailable — review manually"],
            rationale="LLM scoring failed; neutral fallback score applied.",
        )
