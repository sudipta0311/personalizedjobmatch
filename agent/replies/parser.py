"""Reply-command parser — Phase 4.

Parses the user's email reply into structured commands. The grammar (documented
in the digest itself):

    prepare <ids>      e.g.  prepare 1,3
    warm <ids>         e.g.  warm 5
    info <ids>         e.g.  info 2
    skip <ids>         e.g.  skip 4
    ask <id>: <question>

Multiple commands per email are separated by ';' or newlines, e.g.
`prepare 1,3; warm 5; skip 2`. Parsing is:
  * case-insensitive,
  * tolerant of separators/whitespace,
  * quote-aware — it strips the quoted previous email (lines starting with '>',
    everything after an "On ... wrote:" attribution, and the signature),
  * pure — no DB/network, fully unit-testable.

Idempotency (never double-processing a reply) is enforced at the DB layer via the
unique gmail_message_id, not here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

_KEYWORDS = ("prepare", "warm", "info", "skip", "ask")
# First command keyword anywhere in the segment (so "please prepare 1" still parses).
_KW_RE = re.compile(r"\b(prepare|warm|info|skip|ask)\b", re.IGNORECASE)
_ASK_RE = re.compile(r"\s*(\d+)\s*:?\s*(.*)", re.DOTALL)
_INT_RE = re.compile(r"\d+")


@dataclass
class Command:
    command: str               # prepare | warm | info | skip | ask
    ids: list[int] = field(default_factory=list)
    question: str | None = None

    def to_json(self) -> dict[str, Any]:
        return {"command": self.command, "ids": self.ids, "question": self.question}


def strip_quoted(raw: str) -> str:
    """Drop quoted prior email, attribution line, and signature."""
    out: list[str] = []
    for line in (raw or "").splitlines():
        s = line.strip()
        low = s.lower()
        if s.startswith(">"):
            continue
        if low.endswith("wrote:"):          # "On Mon, 27 Jun 2026 ... wrote:"
            break
        if re.match(r"-+\s*original message\s*-+", low):
            break
        if s == "--":                        # signature delimiter
            break
        out.append(line)
    return "\n".join(out)


def _parse_segment(segment: str) -> Command | None:
    m = _KW_RE.search(segment)
    if not m:
        return None
    keyword = m.group(1).lower()
    rest = segment[m.end():]

    if keyword == "ask":
        am = _ASK_RE.match(rest)
        if not am:
            return None
        question = am.group(2).strip() or None
        return Command("ask", [int(am.group(1))], question=question)

    ids = [int(x) for x in _INT_RE.findall(rest)]
    if not ids:
        return None
    # de-dupe within a single command, preserve order
    seen: set[int] = set()
    ordered = [i for i in ids if not (i in seen or seen.add(i))]
    return Command(keyword, ordered)


def _merge(commands: list[Command]) -> list[Command]:
    """Merge repeated non-ask commands (e.g. two 'skip' segments) into one."""
    merged: dict[str, Command] = {}
    asks: list[Command] = []
    order: list[str] = []

    for cmd in commands:
        if cmd.command == "ask":
            asks.append(cmd)
            continue
        if cmd.command not in merged:
            merged[cmd.command] = Command(cmd.command, list(cmd.ids))
            order.append(cmd.command)
        else:
            existing = merged[cmd.command].ids
            for i in cmd.ids:
                if i not in existing:
                    existing.append(i)

    return [merged[k] for k in order] + asks


def parse_reply(raw_text: str) -> list[Command]:
    """Parse an email reply body into a list of commands (empty if none)."""
    cleaned = strip_quoted(raw_text)
    commands: list[Command] = []
    for segment in re.split(r"[;\n]", cleaned):
        if not segment.strip():
            continue
        cmd = _parse_segment(segment)
        if cmd:
            commands.append(cmd)
    return _merge(commands)
