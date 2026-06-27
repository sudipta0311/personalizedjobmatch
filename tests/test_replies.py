"""Tests for Phase 4 — reply command parser, executor dispatch, and Gmail read.

DB writes in the executor are monkeypatched; Gmail message-body extraction is
tested against a hand-built payload.
"""

from __future__ import annotations

import base64

import pytest

from agent.email import gmail
from agent.replies import executor
from agent.replies.parser import Command, parse_reply, strip_quoted


# ---------------------------------------------------------------------------
# Parser — single commands
# ---------------------------------------------------------------------------

def test_parse_single_prepare():
    cmds = parse_reply("prepare 1")
    assert cmds == [Command("prepare", [1])]


def test_parse_ids_comma_and_space():
    cmds = parse_reply("prepare 1, 3 5")
    assert cmds[0].command == "prepare"
    assert cmds[0].ids == [1, 3, 5]


def test_parse_case_insensitive():
    assert parse_reply("PREPARE 2")[0].command == "prepare"
    assert parse_reply("Skip 4")[0] == Command("skip", [4])


def test_parse_multiple_commands_semicolons():
    cmds = parse_reply("prepare 1,3; warm 5; skip 2")
    by = {c.command: c.ids for c in cmds}
    assert by == {"prepare": [1, 3], "warm": [5], "skip": [2]}


def test_parse_multiple_commands_newlines():
    cmds = parse_reply("prepare 1\nwarm 2\nskip 3")
    assert {c.command for c in cmds} == {"prepare", "warm", "skip"}


def test_parse_ask_with_question():
    cmds = parse_reply("ask 4: how should I handle the fine-tuning gap?")
    assert len(cmds) == 1
    assert cmds[0].command == "ask"
    assert cmds[0].ids == [4]
    assert cmds[0].question == "how should I handle the fine-tuning gap?"


def test_parse_ask_preserves_question_case():
    cmds = parse_reply("ask 1: What About GDPR?")
    assert cmds[0].question == "What About GDPR?"


def test_parse_dedupes_ids_within_command():
    cmds = parse_reply("skip 2 2 3")
    assert cmds[0].ids == [2, 3]


def test_parse_merges_repeated_command_types():
    cmds = parse_reply("skip 1; skip 2")
    assert cmds == [Command("skip", [1, 2])]


def test_parse_empty_returns_nothing():
    assert parse_reply("thanks, looks great!") == []


def test_parse_ignores_unknown_words():
    cmds = parse_reply("please prepare 1 when you can")
    assert cmds == [Command("prepare", [1])]


# ---------------------------------------------------------------------------
# Parser — quote stripping
# ---------------------------------------------------------------------------

def test_strip_quoted_drops_gt_lines():
    raw = "prepare 1\n> [2] Some other role\n> warm 2"
    assert "warm 2" not in strip_quoted(raw)
    assert "prepare 1" in strip_quoted(raw)


def test_strip_quoted_breaks_at_attribution():
    raw = "skip 3\nOn Mon, 27 Jun 2026 at 08:00, Agent <a@b.com> wrote:\nprepare 9"
    out = strip_quoted(raw)
    assert "skip 3" in out
    assert "prepare 9" not in out


def test_parse_ignores_quoted_prior_email():
    raw = (
        "prepare 1\n\n"
        "On Mon, 27 Jun 2026, Agent wrote:\n"
        "> [2] Senior AI Architect\n"
        "> warm 2; skip 3\n"
    )
    cmds = parse_reply(raw)
    assert cmds == [Command("prepare", [1])]


def test_strip_quoted_breaks_at_signature():
    raw = "skip 1\n--\nSudipta\nprepare 5"
    out = strip_quoted(raw)
    assert "skip 1" in out
    assert "prepare 5" not in out


# ---------------------------------------------------------------------------
# Executor — resolution + dispatch (persistence monkeypatched)
# ---------------------------------------------------------------------------

@pytest.fixture
def captured(monkeypatch):
    calls = {"status": [], "events": []}
    monkeypatch.setattr(executor.persistence, "set_application_status",
                        lambda job_id, status, note=None: calls["status"].append((job_id, status, note)))
    monkeypatch.setattr(executor.persistence, "log_event",
                        lambda job_id, t, payload: calls["events"].append((job_id, t, payload)))
    monkeypatch.setattr(executor.persistence, "job_titles",
                        lambda ids: {i: f"Title-{i}" for i in ids})
    return calls


INDEX_MAP = {"1": "job-1", "2": "job-2", "3": "job-3"}


def test_resolve_indices_splits_known_unknown():
    resolved, unknown = executor.resolve_indices([1, 9, 3], INDEX_MAP)
    assert resolved == ["job-1", "job-3"]
    assert unknown == [9]


def test_execute_skip_sets_status(captured):
    ack = executor.execute_commands([Command("skip", [1, 2])], INDEX_MAP)
    statuses = {(j, s) for j, s, _ in captured["status"]}
    assert ("job-1", "skipped") in statuses
    assert ("job-2", "skipped") in statuses
    assert any("Skipped" in line for line in ack)


def test_execute_prepare_records_intent(captured):
    ack = executor.execute_commands([Command("prepare", [1])], INDEX_MAP)
    assert ("job-1", "shortlisted", "prepare requested") in captured["status"]
    assert any(e[1] == "prepare_requested" for e in captured["events"])
    assert any("Phase 5" in line for line in ack)


def test_execute_warm_records_intent(captured):
    ack = executor.execute_commands([Command("warm", [2])], INDEX_MAP)
    assert ("job-2", "shortlisted", "warm requested") in captured["status"]
    assert any(e[1] == "warm_requested" for e in captured["events"])


def test_execute_ask_logs_question(captured):
    ack = executor.execute_commands(
        [Command("ask", [3], question="how about GDPR?")], INDEX_MAP
    )
    asks = [e for e in captured["events"] if e[1] == "ask_received"]
    assert asks and asks[0][2]["question"] == "how about GDPR?"


def test_execute_unknown_index_reported(captured):
    ack = executor.execute_commands([Command("skip", [9])], INDEX_MAP)
    assert any("index 9" in line for line in ack)


def test_execute_no_commands_message(captured):
    ack = executor.execute_commands([], INDEX_MAP)
    assert ack == ["No recognised commands found in your reply."]


def test_compose_ack_includes_lines_and_reminder():
    body = executor.compose_ack("me@x.com", ["Skipped: Title-1"])
    assert "Skipped: Title-1" in body
    assert "nothing is submitted automatically" in body


# ---------------------------------------------------------------------------
# Gmail read helpers
# ---------------------------------------------------------------------------

def _b64(s: str) -> str:
    return base64.urlsafe_b64encode(s.encode()).decode()


def test_extract_plain_text_from_multipart():
    message = {
        "snippet": "fallback",
        "payload": {
            "mimeType": "multipart/alternative",
            "parts": [
                {"mimeType": "text/plain", "body": {"data": _b64("prepare 1")}},
                {"mimeType": "text/html", "body": {"data": _b64("<b>prepare 1</b>")}},
            ],
        },
    }
    assert gmail.extract_plain_text(message) == "prepare 1"


def test_extract_plain_text_falls_back_to_snippet():
    message = {"snippet": "skip 2", "payload": {"mimeType": "text/html",
                                                "body": {"data": _b64("<i>x</i>")}}}
    assert gmail.extract_plain_text(message) == "skip 2"


class _FakeUsers:
    def __init__(self, thread):
        self._thread = thread
        self.modified = []

    def threads(self):
        outer = self

        class _T:
            def get(self, userId, id, format):
                class _E:
                    def execute(self_inner):
                        return outer._thread
                return _E()
        return _T()

    def messages(self):
        outer = self

        class _M:
            def modify(self, userId, id, body):
                outer.modified.append((id, body))

                class _E:
                    def execute(self_inner):
                        return {}
                return _E()
        return _M()


class FakeService:
    def __init__(self, thread):
        self._users = _FakeUsers(thread)

    def users(self):
        return self._users


def test_list_unread_replies_filters_unread():
    thread = {
        "messages": [
            {"id": "m0", "labelIds": ["SENT"],
             "payload": {"headers": [], "mimeType": "text/plain",
                         "body": {"data": _b64("the digest")}}},
            {"id": "m1", "labelIds": ["UNREAD", "INBOX"],
             "payload": {"headers": [{"name": "From", "value": "me@x.com"},
                                     {"name": "Subject", "value": "Re: digest"}],
                         "mimeType": "text/plain",
                         "body": {"data": _b64("prepare 1")}}},
        ]
    }
    svc = FakeService(thread)
    replies = gmail.list_unread_replies(svc, "t1")
    assert len(replies) == 1
    assert replies[0]["message_id"] == "m1"
    assert replies[0]["body"] == "prepare 1"
    assert replies[0]["from"] == "me@x.com"


def test_mark_read_removes_unread_label():
    svc = FakeService({"messages": []})
    gmail.mark_read(svc, "m1")
    assert svc._users.modified == [("m1", {"removeLabelIds": ["UNREAD"]})]


def test_list_thread_messages_returns_all():
    """All messages regardless of UNREAD (single-account self-reply case)."""
    thread = {
        "messages": [
            {"id": "m0", "labelIds": ["SENT"],
             "payload": {"headers": [{"name": "Subject", "value": "digest"}],
                         "mimeType": "text/plain", "body": {"data": _b64("the digest")}}},
            {"id": "m1", "labelIds": ["SENT"],   # self-reply: NOT unread
             "payload": {"headers": [{"name": "From", "value": "me@x.com"}],
                         "mimeType": "text/plain", "body": {"data": _b64("prepare 1")}}},
        ]
    }
    svc = FakeService(thread)
    msgs = gmail.list_thread_messages(svc, "t1")
    assert [m["message_id"] for m in msgs] == ["m0", "m1"]
    assert msgs[1]["body"] == "prepare 1"
