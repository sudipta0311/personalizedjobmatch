"""Tests for Phase 3 — digest rendering, index map, and Gmail MIME/send.

No network: Gmail is exercised via build_raw_message (pure) and send_message
against a fake service object.
"""

from __future__ import annotations

import base64
import datetime as _dt
from email import message_from_bytes

import pytest

from agent.email import digest
from agent.email.gmail import build_raw_message, send_message
from agent.scoring.llm_fit import LLMFit
from agent.scoring.rules import RuleResult
from agent.scoring.score import ScoredJob


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _scored(idx: int, *, composite: float, auth_status: str = "blue_card_eligible",
            flags=None, match_points=None, gaps=None) -> ScoredJob:
    job = {
        "id": f"uuid-{idx}",
        "title": f"Senior AI Architect {idx}",
        "company": f"Company{idx}",
        "location": "Amsterdam, Netherlands",
        "country": "NL",
        "url": f"https://jobs.example.com/{idx}",
    }
    rule = RuleResult(
        market_tag="eu",
        auth_status=auth_status,
        auth_fit=85.0,
        grade_fit=100.0,
        required_years=10,
        veto=False,
        veto_reason=None,
        flags=flags or [],
    )
    llm = LLMFit(fit_score=int(composite), match_points=match_points or [],
                 gaps=gaps or [], rationale="solid fit")
    return ScoredJob(
        job=job, rule=rule, llm=llm,
        auth_fit=85.0, grade_fit=100.0, llm_fit=composite,
        composite=composite, rationale="solid fit",
        match_points=match_points or [], gaps=gaps or [],
    )


@pytest.fixture
def shortlist():
    return [
        _scored(1, composite=88, match_points=["agentic match", "Azure stack"],
                gaps=["process-mining domain"]),
        _scored(2, composite=72, auth_status="needs_licensed_sponsor",
                flags=["needs a licensed visa sponsor"]),
    ]


# ---------------------------------------------------------------------------
# index map
# ---------------------------------------------------------------------------

def test_build_index_map(shortlist):
    assert digest.build_index_map(shortlist) == {"1": "uuid-1", "2": "uuid-2"}


# ---------------------------------------------------------------------------
# text rendering
# ---------------------------------------------------------------------------

def test_render_text_has_roles_and_commands(shortlist):
    today = _dt.date(2026, 6, 27)
    text = digest.render_text(shortlist, today)

    assert "2026-06-27" in text
    assert "[1]  Senior AI Architect 1 — Company1 (Amsterdam, Netherlands)" in text
    assert "fit 88" in text
    assert "Why: agentic match; Azure stack" in text
    assert "Gaps: process-mining domain" in text
    assert "https://jobs.example.com/1" in text
    # command menu both per-role and in the help block
    assert "prepare 1" in text
    assert "prepare <ids>" in text
    assert "ask <id>: <your question>" in text


def test_render_text_auth_markers(shortlist):
    text = digest.render_text(shortlist)
    assert "[Blue-Card eligible]" in text
    assert "[needs licensed sponsor]" in text


def test_render_text_falls_back_to_rationale_when_no_match_points():
    s = _scored(9, composite=65, match_points=[])
    text = digest.render_text([s])
    assert "Why: solid fit" in text


# ---------------------------------------------------------------------------
# html rendering
# ---------------------------------------------------------------------------

def test_render_html_escapes_and_links(shortlist):
    html_body = digest.render_html(shortlist)
    assert "<a href=\"https://jobs.example.com/1\">View posting</a>" in html_body
    assert "fit 88" in html_body
    assert "<code>prepare 1</code>" in html_body


def test_render_html_escapes_special_chars():
    s = _scored(1, composite=80, match_points=["R&D <lead> role"])
    html_body = digest.render_html([s])
    assert "R&amp;D &lt;lead&gt; role" in html_body
    assert "<lead>" not in html_body


# ---------------------------------------------------------------------------
# subject + full render
# ---------------------------------------------------------------------------

def test_render_digest_returns_all_parts(shortlist):
    subject, text_body, html_body, index_map = digest.render_digest(
        shortlist, _dt.date(2026, 6, 27)
    )
    assert "2 roles" in subject
    assert "2026-06-27" in subject
    assert "prepare 1" in text_body
    assert "View posting" in html_body
    assert index_map == {"1": "uuid-1", "2": "uuid-2"}


def test_subject_singular_for_one_role():
    s = _scored(1, composite=90)
    subject, *_ = digest.render_digest([s], _dt.date(2026, 6, 27))
    assert "1 role " in subject  # trailing space => not "roles"


# ---------------------------------------------------------------------------
# Gmail MIME
# ---------------------------------------------------------------------------

def test_build_raw_message_plain_only():
    body = build_raw_message("me@x.com", "Hi", "hello world")
    raw = base64.urlsafe_b64decode(body["raw"])
    msg = message_from_bytes(raw)
    assert msg["To"] == "me@x.com"
    assert msg["Subject"] == "Hi"
    assert not msg.is_multipart()
    assert "hello world" in msg.get_payload(decode=True).decode()


def test_build_raw_message_multipart_alternative():
    body = build_raw_message("me@x.com", "Hi", "plain text", "<b>html</b>")
    raw = base64.urlsafe_b64decode(body["raw"])
    msg = message_from_bytes(raw)
    assert msg.is_multipart()
    types = [p.get_content_type() for p in msg.get_payload()]
    assert types == ["text/plain", "text/html"]


def test_build_raw_message_thread_headers():
    body = build_raw_message(
        "me@x.com", "Hi", "x",
        thread_headers={"In-Reply-To": "<abc@mail>", "References": "<abc@mail>"},
    )
    msg = message_from_bytes(base64.urlsafe_b64decode(body["raw"]))
    assert msg["In-Reply-To"] == "<abc@mail>"


# ---------------------------------------------------------------------------
# Gmail send (fake service)
# ---------------------------------------------------------------------------

class FakeGmailService:
    def __init__(self, result):
        self._result = result
        self.sent_body = None

    def users(self):
        return self

    def messages(self):
        return self

    def send(self, userId, body):  # noqa: N803 - mirrors google api kwarg
        self.sent_body = body
        outer = self

        class _Exec:
            def execute(self_inner):
                return outer._result

        return _Exec()


def test_send_message_returns_ids():
    svc = FakeGmailService({"id": "m1", "threadId": "t1"})
    msg_id, thread_id = send_message(svc, "to@x.com", "Subj", "body", "<i>body</i>")
    assert (msg_id, thread_id) == ("m1", "t1")
    assert "raw" in svc.sent_body
