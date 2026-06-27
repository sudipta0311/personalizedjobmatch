"""Tailoring content generation (LLM) — Phase 5.

Produces, for one role, the *text* of a tailored application package:
  * a tailored professional summary (drawn ONLY from the master profile),
  * a cover letter in the user's voice (honest about gaps),
  * pre-written answers to common form questions,
  * a ranked list of JD keywords to emphasise.

CRITICAL — never fabricate. The CV bullets themselves are rendered verbatim from
profile.yaml (see docx_builder); the LLM only writes the summary/letter/answers
and ranks keywords. Provider + model come from profile.models.tailoring
(Anthropic/Opus by default), via the provider-agnostic agent.llm.client.
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel, Field

from agent.llm.client import parse_structured, resolve_node

logger = logging.getLogger(__name__)

_MAX_JD_CHARS = 12_000


class FormAnswers(BaseModel):
    work_authorisation: str = Field(description="Answer to a work-authorisation question.")
    notice_period: str = Field(description="Notice period / availability.")
    relocation: str = Field(description="Relocation / remote stance.")
    why_this_role: str = Field(description="Concise 'why this role / company' answer.")


class TailoredContent(BaseModel):
    summary: str = Field(description="Tailored professional summary, from the candidate's real content only.")
    cover_letter: str = Field(description="Tailored cover letter in the candidate's voice, <350 words.")
    form_answers: FormAnswers
    emphasis_keywords: list[str] = Field(description="JD keywords the candidate genuinely matches.")


def _system_prompt(profile: dict[str, Any], market_tag: str) -> str:
    personal = profile.get("personal", {})
    cv = profile.get("cv_content", {})
    framing = (profile.get("market_framings", {}) or {}).get(market_tag, {}) \
        or (profile.get("market_framings", {}) or {}).get("eu", {})
    style = profile.get("letter_style", {})
    prefs = (profile.get("job_preferences") or "").strip()

    avoid = "; ".join(style.get("avoid_phrases", []))
    return (
        "You write tailored job-application materials for a senior candidate. "
        "Use ONLY the candidate's real information below — never invent employers, "
        "skills, dates, or achievements. Be honest about gaps (one sentence max).\n\n"
        f"CANDIDATE: {personal.get('name')} | {personal.get('email')} | "
        f"{personal.get('location')}\n"
        f"MASTER SUMMARY: {(cv.get('summary') or '').strip()}\n"
        f"POSITIONING ({market_tag}): {(framing.get('positioning') or '').strip()}\n"
        f"SKILLS: {_flatten_skills(cv.get('skills', {}))}\n"
        f"CERTIFICATIONS: {_format_certs(cv.get('certifications', []))}\n"
        f"EXPERIENCE: {_format_experience(cv.get('experience', []))}\n"
        f"PREFERENCES: {prefs}\n"
        f"NOTICE PERIOD: {profile.get('notice_period')}; "
        f"RELOCATION OPEN: {profile.get('relocation_open')}; "
        f"REMOTE PREF: {profile.get('remote_preference')}\n"
        f"VOICE NOTES: {(style.get('voice_notes') or '').strip()} "
        f"Tone: {style.get('tone')}. Length: {style.get('length')}. "
        f"AVOID these phrases: {avoid}\n\n"
        "Respond with ONLY a JSON object with keys: summary (string), "
        "cover_letter (string), form_answers (object with work_authorisation, "
        "notice_period, relocation, why_this_role), emphasis_keywords (array of strings)."
    )


def _flatten_skills(skills: dict[str, list[str]]) -> str:
    out: list[str] = []
    for group in skills.values():
        out.extend(group)
    return ", ".join(out)


def _format_certs(certs: list[dict[str, Any]]) -> str:
    return ", ".join(c.get("name", "") for c in certs)


def _format_experience(exp: list[dict[str, Any]]) -> str:
    parts = []
    for e in exp:
        bullets = " | ".join(e.get("bullets", []))
        parts.append(
            f"[{e.get('title')} @ {e.get('company')} "
            f"({e.get('start')}–{e.get('end') or 'present'}): {bullets}]"
        )
    return " ".join(parts)


def _user_prompt(job: dict[str, Any]) -> str:
    jd = (job.get("jd_text") or "")[:_MAX_JD_CHARS]
    return (
        f"ROLE: {job.get('title')} at {job.get('company')} "
        f"({job.get('location') or 'location n/a'})\n\n"
        f"JOB DESCRIPTION:\n{jd}\n\n"
        "Write the tailored summary, cover letter, form answers, and emphasis "
        "keywords for THIS role. Return the JSON object."
    )


def generate_tailored(
    profile: dict[str, Any],
    job: dict[str, Any],
    *,
    client: Any | None = None,
    provider: str | None = None,
    model: str | None = None,
) -> TailoredContent:
    """Generate the tailored text package for one role."""
    node_provider, node_model = resolve_node(profile, "tailoring")
    provider = provider or node_provider
    model = model or node_model
    return parse_structured(
        provider=provider,
        model=model,
        system=_system_prompt(profile, job.get("market_tag") or "eu"),
        user=_user_prompt(job),
        schema_model=TailoredContent,
        client=client,
        max_tokens=2048,
    )
