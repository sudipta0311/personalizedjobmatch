"""Command executor — Phase 4 + Phase 5.

Dispatches parsed commands against the digest's index_map.

  * skip    — application -> 'skipped'
  * prepare — generate a tailored ATS package (CV + cover letter + form answers),
              parse-verify it, persist, and email it back as attachments
  * warm    — generate a LinkedIn play (boolean search + public contacts +
              outreach drafts) and email it as a manual-execution checklist
  * info    — recorded (deeper brief: Phase 6)
  * ask     — recorded with the question (answered: Phase 6)

prepare/warm only run when an ExecContext is supplied (it carries the profile +
email senders + LLM client). Without one — e.g. in unit tests — they fall back to
recording intent, so the dispatch stays testable offline. The generators are
injectable on the context so the heavy LLM/docx work is mocked in tests.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable

from agent.replies import persistence
from agent.replies.parser import Command

logger = logging.getLogger(__name__)


@dataclass
class ExecContext:
    """Everything prepare/warm need to actually produce + deliver artifacts."""

    profile: dict[str, Any]
    send_text: Callable[[str, str], None]                  # (subject, body)
    send_attachments: Callable[[str, str, list[str]], None]  # (subject, body, paths)
    client: Any = None
    out_dir: str = "output"
    build_package: Callable[..., Any] = field(default=None)   # type: ignore[assignment]
    build_play: Callable[..., Any] = field(default=None)      # type: ignore[assignment]
    render_play_email: Callable[..., str] = field(default=None)  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.build_package is None:
            from agent.tailor.package import build_package
            self.build_package = build_package
        if self.build_play is None:
            from agent.linkedin_play.generate import build_linkedin_play
            self.build_play = build_linkedin_play
        if self.render_play_email is None:
            from agent.linkedin_play.generate import render_play_email
            self.render_play_email = render_play_email


def resolve_indices(
    ids: list[int], index_map: dict[str, Any]
) -> tuple[list[Any], list[int]]:
    """Split reply indices into (resolved job_ids, unknown indices)."""
    resolved: list[Any] = []
    unknown: list[int] = []
    for i in ids:
        job_id = index_map.get(str(i))
        if job_id is None:
            unknown.append(i)
        else:
            resolved.append(job_id)
    return resolved, unknown


def _labels(job_ids: list[Any]) -> str:
    titles = persistence.job_titles(job_ids)
    return ", ".join(str(titles.get(j, j)) for j in job_ids)


def _unknown_lines(unknown: list[int]) -> list[str]:
    return [f"Couldn't find a role for index {i} in that digest." for i in unknown]


def execute_commands(
    commands: list[Command],
    index_map: dict[str, Any],
    *,
    ctx: ExecContext | None = None,
) -> list[str]:
    """Execute each command; return acknowledgement lines for the reply email."""
    ack: list[str] = []

    for cmd in commands:
        job_ids, unknown = resolve_indices(cmd.ids, index_map)

        if cmd.command == "ask":
            for job_id in job_ids:
                persistence.log_event(job_id, "ask_received", {"question": cmd.question})
            ack.append(
                f"Noted your question (answered in a later phase): "
                f"{cmd.question or '(no question text)'}"
            )

        elif cmd.command == "skip":
            for job_id in job_ids:
                persistence.set_application_status(job_id, "skipped", "skipped via reply")
            if job_ids:
                ack.append(f"Skipped: {_labels(job_ids)}")

        elif cmd.command == "prepare":
            ack.extend(_run_prepare(job_ids, ctx))

        elif cmd.command == "warm":
            ack.extend(_run_warm(job_ids, ctx))

        elif cmd.command == "info":
            for job_id in job_ids:
                persistence.log_event(job_id, "info_requested", {})
            if job_ids:
                ack.append(
                    f"Company brief requested (arrives in a later phase): {_labels(job_ids)}"
                )

        ack.extend(_unknown_lines(unknown))

    if not ack:
        ack.append("No recognised commands found in your reply.")
    return ack


# ---------------------------------------------------------------------------
# prepare / warm
# ---------------------------------------------------------------------------

def _run_prepare(job_ids: list[Any], ctx: ExecContext | None) -> list[str]:
    if not job_ids:
        return []
    if ctx is None:
        # Phase-4 fallback (no context): just record intent.
        for job_id in job_ids:
            persistence.set_application_status(job_id, "shortlisted", "prepare requested")
            persistence.log_event(job_id, "prepare_requested", {})
        return [f"Queued for prepare: {_labels(job_ids)}"]

    ack: list[str] = []
    jobs = persistence.get_jobs(job_ids)
    for job_id in job_ids:
        job = jobs.get(str(job_id))
        if not job:
            ack.append(f"Couldn't load job {job_id} to prepare.")
            continue
        try:
            pkg = ctx.build_package(ctx.profile, job, out_dir=ctx.out_dir, client=ctx.client)
            persistence.set_application_prepared(job_id, pkg.cv_path, pkg.letter_path, pkg.answers)
            body = _prepare_body(job, pkg)
            subject = f"Application package: {job.get('title')} — {job.get('company')}"
            ctx.send_attachments(subject, body, [pkg.cv_path, pkg.letter_path])
            status = "verified" if pkg.verify_ok else f"FLAGGED (missing: {', '.join(pkg.verify_missing)})"
            ack.append(f"Prepared & emailed package for {job.get('title')} — parse-check: {status}")
        except Exception as exc:  # noqa: BLE001 - one bad role mustn't crash the run
            logger.error("prepare failed for %s: %s", job.get("title"), exc)
            ack.append(f"Couldn't prepare {job.get('title')}: {exc}")
    return ack


def _prepare_body(job: dict[str, Any], pkg: Any) -> str:
    a = pkg.answers
    verify = (
        "Parse-verification: PASSED — name, contact, all roles, dates, and skills "
        "were recovered from the generated CV."
        if pkg.verify_ok
        else f"Parse-verification: FLAGGED — review these missing items: {', '.join(pkg.verify_missing)}"
    )
    return (
        f"Tailored application package for {job.get('title')} at {job.get('company')}.\n"
        "Attached: ATS-optimised CV + cover letter (.docx). Review before submitting — "
        "nothing is submitted automatically.\n\n"
        f"{verify}\n\n"
        "Pre-written form answers:\n"
        f"  - Work authorisation: {a.get('work_authorisation')}\n"
        f"  - Notice period: {a.get('notice_period')}\n"
        f"  - Relocation: {a.get('relocation')}\n"
        f"  - Why this role: {a.get('why_this_role')}\n"
    )


def _run_warm(job_ids: list[Any], ctx: ExecContext | None) -> list[str]:
    if not job_ids:
        return []
    if ctx is None:
        for job_id in job_ids:
            persistence.set_application_status(job_id, "shortlisted", "warm requested")
            persistence.log_event(job_id, "warm_requested", {})
        return [f"Queued for warm: {_labels(job_ids)}"]

    ack: list[str] = []
    jobs = persistence.get_jobs(job_ids)
    match_points_map = persistence.get_match_points(job_ids)
    for job_id in job_ids:
        job = jobs.get(str(job_id))
        if not job:
            ack.append(f"Couldn't load job {job_id} for warm.")
            continue
        try:
            mp = match_points_map.get(str(job_id), [])
            play = ctx.build_play(ctx.profile, job, client=ctx.client, match_points=mp)
            persistence.set_application_warm(job_id, play)
            body = ctx.render_play_email(job, play)
            subject = f"LinkedIn play (manual): {job.get('title')} — {job.get('company')}"
            ctx.send_text(subject, body)
            ack.append(f"LinkedIn play emailed (do manually) for {job.get('title')}")
        except Exception as exc:  # noqa: BLE001 - one bad role mustn't crash the run
            logger.error("warm failed for %s: %s", job.get("title"), exc)
            ack.append(f"Couldn't build LinkedIn play for {job.get('title')}: {exc}")
    return ack


def compose_ack(reply_from: str, ack_lines: list[str]) -> str:
    """Compose the acknowledgement email body."""
    body = ["Processed your reply. Here's what I did:", ""]
    body.extend(f"  - {line}" for line in ack_lines)
    body.append("")
    body.append(
        "Reminder: nothing is submitted automatically. prepare/warm produce drafts "
        "for you to review; all LinkedIn actions are manual."
    )
    return "\n".join(body)
