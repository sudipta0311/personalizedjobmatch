"""Command executor — Phase 4.

Dispatches parsed commands against the digest's index_map. In v1:
  * skip    — fully actioned (application -> 'skipped')
  * prepare — recorded as intent (application -> 'shortlisted'); the actual
              ATS package is generated in Phase 5
  * warm    — recorded as intent; the LinkedIn play is generated in Phase 5
  * info    — recorded; deeper company brief arrives in Phase 6
  * ask     — recorded with the question; answered in Phase 6

Returns human-readable acknowledgement lines that the poller emails back. The DB
writes go through agent.replies.persistence (monkeypatched in tests).
"""

from __future__ import annotations

import logging
from typing import Any

from agent.replies import persistence
from agent.replies.parser import Command

logger = logging.getLogger(__name__)


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


def execute_commands(commands: list[Command], index_map: dict[str, Any]) -> list[str]:
    """Execute each command; return acknowledgement lines for the reply email."""
    ack: list[str] = []

    for cmd in commands:
        if cmd.command == "ask":
            job_ids, unknown = resolve_indices(cmd.ids, index_map)
            for job_id in job_ids:
                persistence.log_event(job_id, "ask_received", {"question": cmd.question})
            ack.append(
                f"Noted your question (answered in a later phase): "
                f"{cmd.question or '(no question text)'}"
            )
            ack.extend(_unknown_lines(unknown))
            continue

        job_ids, unknown = resolve_indices(cmd.ids, index_map)

        if cmd.command == "skip":
            for job_id in job_ids:
                persistence.set_application_status(job_id, "skipped", "skipped via reply")
            if job_ids:
                ack.append(f"Skipped: {_labels(job_ids)}")

        elif cmd.command == "prepare":
            for job_id in job_ids:
                persistence.set_application_status(job_id, "shortlisted", "prepare requested")
                persistence.log_event(job_id, "prepare_requested", {})
            if job_ids:
                ack.append(
                    "Queued for prepare — the tailored ATS package generation "
                    f"arrives in Phase 5: {_labels(job_ids)}"
                )

        elif cmd.command == "warm":
            for job_id in job_ids:
                persistence.set_application_status(job_id, "shortlisted", "warm requested")
                persistence.log_event(job_id, "warm_requested", {})
            if job_ids:
                ack.append(
                    "Queued for warm — the LinkedIn play (search + public contacts "
                    f"+ outreach drafts) arrives in Phase 5: {_labels(job_ids)}"
                )

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


def _unknown_lines(unknown: list[int]) -> list[str]:
    return [f"Couldn't find a role for index {i} in that digest." for i in unknown]


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
