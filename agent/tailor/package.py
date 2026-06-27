"""Prepare-package orchestration — Phase 5.

Ties together: generate tailored text (LLM) → render ATS CV + cover letter
(.docx) → parse-verify the CV. Returns everything the reply poller needs to
email the package back and persist the application row.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from agent.tailor.docx_builder import build_cover_letter, build_cv
from agent.tailor.generate import TailoredContent, generate_tailored
from agent.tailor.verify import verify_cv

logger = logging.getLogger(__name__)


@dataclass
class Package:
    job_id: Any
    cv_path: str
    letter_path: str
    answers: dict[str, str]
    verify_ok: bool
    verify_missing: list[str] = field(default_factory=list)


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (text or "").lower()).strip("_")[:40] or "role"


def build_package(
    profile: dict[str, Any],
    job: dict[str, Any],
    *,
    out_dir: str = "output",
    client: Any | None = None,
) -> Package:
    """Generate + render + verify a tailored application package for one role."""
    tailored: TailoredContent = generate_tailored(profile, job, client=client)

    base = f"{_slug(profile.get('personal', {}).get('name', 'cv'))}_{_slug(job.get('company'))}_{job.get('id')}"
    cv_path = f"{out_dir}/{base}_CV.docx"
    letter_path = f"{out_dir}/{base}_CoverLetter.docx"

    build_cv(profile, job, tailored, cv_path)
    build_cover_letter(profile, job, tailored, letter_path)

    ok, missing = verify_cv(cv_path, profile)
    if not ok:
        logger.warning("Parse-verify found missing fields in %s: %s", cv_path, missing)

    fa = tailored.form_answers
    answers = {
        "work_authorisation": fa.work_authorisation,
        "notice_period": fa.notice_period,
        "relocation": fa.relocation,
        "why_this_role": fa.why_this_role,
    }
    return Package(
        job_id=job.get("id"),
        cv_path=cv_path,
        letter_path=letter_path,
        answers=answers,
        verify_ok=ok,
        verify_missing=missing,
    )
