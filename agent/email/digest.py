"""Weekly digest rendering — Phase 3.

Turns the filtered shortlist (list[ScoredJob]) into:
  * an index_map  {"1": job_id, "2": job_id, ...}  — stored so replies resolve,
  * a plain-text body (the control channel — the command syntax lives here),
  * an HTML body (nicer to read; same content).

Every role carries everything the user needs to act manually WITHOUT replying
(title, company, location, score, why, gaps, auth flag, posting URL) plus the
reply-command menu. Pure functions — no DB, no network.
"""

from __future__ import annotations

import datetime as _dt
import html
from typing import Any

from agent.scoring.score import ScoredJob

_COMMANDS_HELP = (
    "Reply to this email with commands (one or many, e.g. "
    "`prepare 1,3; warm 5; skip 2`):\n"
    "  prepare <ids>  build a tailored ATS application package\n"
    "  warm <ids>     build a LinkedIn play (search + contacts + outreach drafts)\n"
    "  info <ids>     deeper company brief\n"
    "  skip <ids>     dismiss\n"
    "  ask <id>: <your question>\n"
)


def build_index_map(shortlist: list[ScoredJob]) -> dict[str, Any]:
    """Map the 1-based reply index to each role's job_id."""
    return {str(i): s.job_id for i, s in enumerate(shortlist, start=1)}


def _location(job: dict[str, Any]) -> str:
    return job.get("location") or "location n/a"


def _auth_marker(s: ScoredJob) -> str:
    status = s.rule.auth_status
    if status in ("authorised", "citizen"):
        return "[OK auth]"
    if status == "blue_card_eligible":
        return "[Blue-Card eligible]"
    if status == "needs_licensed_sponsor":
        return "[needs licensed sponsor]"
    if status == "needs_visa":
        return "[visa sponsorship needed]"
    if status == "unknown":
        return "[auth: verify]"
    return ""


def _subject(shortlist: list[ScoredJob], today: _dt.date) -> str:
    n = len(shortlist)
    roles = "role" if n == 1 else "roles"
    return f"Job digest — {today.isoformat()} — {n} {roles} (reply: prepare/warm/info/skip <ids>)"


# ---------------------------------------------------------------------------
# Plain text
# ---------------------------------------------------------------------------

def render_text(shortlist: list[ScoredJob], today: _dt.date | None = None) -> str:
    today = today or _dt.date.today()
    lines: list[str] = [
        f"Your weekly job digest — {today.isoformat()}",
        f"{len(shortlist)} role(s) made the shortlist.",
        "",
    ]

    for i, s in enumerate(shortlist, start=1):
        job = s.job
        marker = _auth_marker(s)
        header = (
            f"[{i}]  {job.get('title')} — {job.get('company')} "
            f"({_location(job)})   fit {s.composite:.0f}"
        )
        if marker:
            header += f"  {marker}"
        lines.append(header)

        if s.match_points:
            lines.append(f"     Why: {'; '.join(s.match_points)}")
        elif s.rationale:
            lines.append(f"     Why: {s.rationale}")
        if s.gaps:
            lines.append(f"     Gaps: {'; '.join(s.gaps)}")
        if s.rule.flags:
            lines.append(f"     Flags: {'; '.join(s.rule.flags)}")
        lines.append(f"     Posting: {job.get('url')}")
        lines.append(
            f"     > prepare {i}   |   warm {i}   |   info {i}   |   skip {i}"
        )
        lines.append("")

    lines.append("-" * 60)
    lines.append(_COMMANDS_HELP)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------

def render_html(shortlist: list[ScoredJob], today: _dt.date | None = None) -> str:
    today = today or _dt.date.today()
    e = html.escape

    parts: list[str] = [
        "<div style=\"font-family:Arial,Helvetica,sans-serif;font-size:14px;"
        "color:#222;max-width:680px\">",
        f"<h2 style=\"margin:0 0 4px\">Your weekly job digest — {today.isoformat()}</h2>",
        f"<p style=\"margin:0 0 16px;color:#666\">{len(shortlist)} role(s) "
        "made the shortlist.</p>",
    ]

    for i, s in enumerate(shortlist, start=1):
        job = s.job
        marker = _auth_marker(s)
        marker_html = (
            f" <span style=\"color:#0a7d2c\">{e(marker)}</span>" if marker else ""
        )
        parts.append("<div style=\"border-top:1px solid #eee;padding:12px 0\">")
        parts.append(
            f"<div style=\"font-weight:bold\">[{i}] {e(str(job.get('title')))} — "
            f"{e(str(job.get('company')))} "
            f"<span style=\"color:#666;font-weight:normal\">({e(_location(job))})</span> "
            f"<span style=\"color:#1155cc\">fit {s.composite:.0f}</span>{marker_html}</div>"
        )
        why = "; ".join(s.match_points) if s.match_points else (s.rationale or "")
        if why:
            parts.append(f"<div><b>Why:</b> {e(why)}</div>")
        if s.gaps:
            parts.append(f"<div><b>Gaps:</b> {e('; '.join(s.gaps))}</div>")
        if s.rule.flags:
            parts.append(
                f"<div style=\"color:#a15c00\"><b>Flags:</b> {e('; '.join(s.rule.flags))}</div>"
            )
        url = str(job.get("url") or "")
        parts.append(
            f"<div><a href=\"{e(url)}\">View posting</a></div>"
        )
        parts.append(
            f"<div style=\"margin-top:4px;color:#444\">Reply: "
            f"<code>prepare {i}</code> &nbsp; <code>warm {i}</code> &nbsp; "
            f"<code>info {i}</code> &nbsp; <code>skip {i}</code></div>"
        )
        parts.append("</div>")

    parts.append(
        "<div style=\"border-top:2px solid #ddd;margin-top:16px;padding-top:12px;"
        "white-space:pre-wrap;color:#555;font-size:13px\">"
        f"{e(_COMMANDS_HELP)}</div>"
    )
    parts.append("</div>")
    return "".join(parts)


def render_digest(
    shortlist: list[ScoredJob],
    today: _dt.date | None = None,
) -> tuple[str, str, str, dict[str, Any]]:
    """Return (subject, text_body, html_body, index_map)."""
    today = today or _dt.date.today()
    subject = _subject(shortlist, today)
    return (
        subject,
        render_text(shortlist, today),
        render_html(shortlist, today),
        build_index_map(shortlist),
    )
