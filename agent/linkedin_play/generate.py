"""LinkedIn play (warm) — Phase 5. MANUAL-EXECUTION ONLY.

⛔ The agent performs NO LinkedIn calls — no login, search, scraping, or actions.
This module produces *drafts the human executes manually in their own browser*,
from PUBLIC / ATS sources only:
  * a boolean LinkedIn search string (built deterministically from the posting),
  * a candidate contact list derived from the PUBLIC job posting (emails in the
    JD, named recruiter/hiring contact),
  * a drafted connection note (<=300 chars) and a follow-up, in the user's voice.

The output is a checklist clearly labelled for manual execution.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from pydantic import BaseModel, Field

from agent.llm.client import parse_structured, resolve_node

logger = logging.getLogger(__name__)

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_MAX_JD_CHARS = 8000
_CONNECTION_NOTE_LIMIT = 300


class Outreach(BaseModel):
    connection_note: str = Field(description="LinkedIn connection note, <=300 characters.")
    follow_up: str = Field(description="Longer follow-up message once connected.")


def boolean_search_string(job: dict[str, Any]) -> str:
    """A boolean string the user can paste into LinkedIn's people search."""
    company = (job.get("company") or "").strip()
    roles = '("recruiter" OR "talent acquisition" OR "hiring manager" OR "head of AI" OR "engineering manager")'
    company_clause = f'"{company}"' if company else ""
    return f'{company_clause} AND {roles}'.strip(" AND ")


def public_contacts(job: dict[str, Any]) -> list[dict[str, str]]:
    """Contacts derivable from the PUBLIC posting only (no LinkedIn)."""
    contacts: list[dict[str, str]] = []
    seen: set[str] = set()
    for email in _EMAIL_RE.findall(job.get("jd_text") or ""):
        if email.lower() in seen:
            continue
        seen.add(email.lower())
        contacts.append({"type": "email", "value": email, "source": "job posting"})
    return contacts


def _outreach(profile: dict[str, Any], job: dict[str, Any], client: Any | None) -> Outreach:
    personal = profile.get("personal", {})
    cv = profile.get("cv_content", {})
    style = profile.get("letter_style", {})
    provider, model = resolve_node(profile, "outreach")
    system = (
        "You draft short, genuine LinkedIn outreach in the candidate's voice. "
        "No flattery, no buzzwords. Reference the specific role. The connection "
        f"note MUST be <= {_CONNECTION_NOTE_LIMIT} characters.\n\n"
        f"CANDIDATE: {personal.get('name')} — {(cv.get('summary') or '').strip()[:300]}\n"
        f"VOICE: {(style.get('voice_notes') or '').strip()}\n\n"
        "Respond with ONLY a JSON object: connection_note (string), follow_up (string)."
    )
    user = (
        f"ROLE: {job.get('title')} at {job.get('company')} "
        f"({job.get('location') or ''}). Draft a connection note and a follow-up."
    )
    return parse_structured(
        provider=provider, model=model, system=system, user=user,
        schema_model=Outreach, client=client, max_tokens=600,
    )


def build_linkedin_play(
    profile: dict[str, Any],
    job: dict[str, Any],
    *,
    client: Any | None = None,
) -> dict[str, Any]:
    """Return the manual-execution LinkedIn play for one role."""
    outreach = _outreach(profile, job, client)
    note = outreach.connection_note.strip()[:_CONNECTION_NOTE_LIMIT]
    return {
        "manual_only": True,
        "search_string": boolean_search_string(job),
        "contacts": public_contacts(job),
        "connection_note": note,
        "follow_up": outreach.follow_up.strip(),
    }


def render_play_email(job: dict[str, Any], play: dict[str, Any]) -> str:
    """Format the play as a manual-action checklist for the reply email."""
    contacts = play.get("contacts", [])
    contact_lines = (
        "\n".join(f"    - {c['value']} ({c['source']})" for c in contacts)
        if contacts else "    - none found in the public posting — use the search string above"
    )
    return (
        f"LinkedIn play for: {job.get('title')} — {job.get('company')}\n"
        "DO THESE MANUALLY IN YOUR OWN LINKEDIN BROWSER SESSION. "
        "The agent does not touch LinkedIn.\n\n"
        "1) Paste this boolean search into LinkedIn people search:\n"
        f"    {play.get('search_string')}\n\n"
        "2) Candidate contacts from the public posting:\n"
        f"{contact_lines}\n\n"
        "3) Connection note (<=300 chars) — copy/paste:\n"
        f"    {play.get('connection_note')}\n\n"
        "4) Follow-up message once connected:\n"
        f"    {play.get('follow_up')}\n"
    )
