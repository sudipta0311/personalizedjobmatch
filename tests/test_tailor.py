"""Tests for Phase 5 — ATS docx rendering, parse-verify, and the LinkedIn play.

No network: the LLM is exercised via injected fake clients; docx build/verify are
pure file operations.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent.linkedin_play.generate import (
    Outreach,
    boolean_search_string,
    build_linkedin_play,
    public_contacts,
    render_play_email,
)
from agent.tailor.docx_builder import build_cover_letter, build_cv
from agent.tailor.generate import FormAnswers, TailoredContent, generate_tailored
from agent.tailor.verify import verify_cv


@pytest.fixture
def profile():
    return {
        "personal": {
            "name": "Sudipta Sarkar",
            "email": "sudipta0311@gmail.com",
            "phone": "+45 91 62 90 54",
            "location": "Copenhagen, Denmark",
            "linkedin_url": "https://linkedin.com/in/test",
        },
        "market_framings": {
            "eu": {"cv_headline": "Senior AI Architect", "positioning": "EU positioning"},
        },
        "letter_style": {"voice_notes": "direct", "tone": "confident",
                         "length": "3 paras", "avoid_phrases": ["thrilled"]},
        "cv_content": {
            "summary": "Senior AI architect.",
            "experience": [
                {"id": "exp-001", "title": "Senior AI Architect", "company": "Acme Corp",
                 "location": "Copenhagen", "start": "2021-01", "end": None,
                 "bullets": ["Built agentic platform.", "Led RAG pipeline."]},
            ],
            "education": [{"degree": "MSc CS", "institution": "Uni", "year": 2009}],
            "certifications": [{"name": "Azure Architect", "issuer": "MS", "year": 2022}],
            "skills": {"ai_ml": ["LangGraph", "RAG"], "cloud": ["Azure", "AWS"]},
            "languages": [{"language": "English", "level": "C2"}],
        },
        "notice_period": "1 month",
        "relocation_open": True,
        "remote_preference": "hybrid",
        "models": {"tailoring": {"provider": "openai", "model": "gpt-4o-mini"},
                   "outreach": {"provider": "openai", "model": "gpt-4o-mini"}},
    }


@pytest.fixture
def tailored():
    return TailoredContent(
        summary="Tailored summary for the role.",
        cover_letter="Para one.\n\nPara two.",
        form_answers=FormAnswers(work_authorisation="EU Blue Card eligible",
                                 notice_period="1 month", relocation="Open",
                                 why_this_role="Strong agentic AI fit."),
        emphasis_keywords=["agentic AI", "RAG", "Azure"],
    )


JOB = {"id": "job-1", "title": "Senior AI Architect", "company": "EPAM",
       "location": "Amsterdam, Netherlands", "market_tag": "eu",
       "jd_text": "Contact careers@epam.com or recruiter jane@epam.com. LLM, RAG."}


# ---------------------------------------------------------------------------
# docx build + parse-verify
# ---------------------------------------------------------------------------

def test_build_cv_and_verify_passes(tmp_path, profile, tailored):
    path = str(tmp_path / "cv.docx")
    build_cv(profile, JOB, tailored, path)
    ok, missing = verify_cv(path, profile)
    assert ok, f"unexpected missing: {missing}"


def test_verify_detects_missing(tmp_path, profile, tailored):
    path = str(tmp_path / "cv.docx")
    build_cv(profile, JOB, tailored, path)
    # Inject a field that isn't in the CV
    profile["personal"]["name"] = "Someone Not In The CV"
    ok, missing = verify_cv(path, profile)
    assert not ok
    assert any("name" in m for m in missing)


def test_build_cover_letter(tmp_path, profile, tailored):
    path = str(tmp_path / "letter.docx")
    build_cover_letter(profile, JOB, tailored, path)
    from docx import Document
    text = "\n".join(p.text for p in Document(path).paragraphs)
    assert "Para one." in text
    assert "EPAM" in text


# ---------------------------------------------------------------------------
# tailored content generation (fake client)
# ---------------------------------------------------------------------------

def _fake_openai(json_str: str):
    class _CC:
        def create(self, **kwargs):
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=json_str))]
            )
    return SimpleNamespace(chat=SimpleNamespace(completions=_CC()))


def test_generate_tailored_parses(profile, tailored):
    client = _fake_openai(tailored.model_dump_json())
    out = generate_tailored(profile, JOB, client=client, provider="openai")
    assert out.summary == "Tailored summary for the role."
    assert out.form_answers.work_authorisation == "EU Blue Card eligible"


# ---------------------------------------------------------------------------
# LinkedIn play (manual-only) — boolean_search_string
# ---------------------------------------------------------------------------

def test_boolean_search_string_ai_role_includes_company_and_ai_terms():
    """AI/ML title → AI decision-maker role terms + company clause."""
    job = {"title": "Senior AI Architect", "company": "EPAM",
           "location": "Amsterdam, Netherlands", "jd_text": ""}
    s = boolean_search_string(job)
    assert '"EPAM"' in s
    assert "head of AI" in s
    assert "technical recruiter" in s


def test_boolean_search_string_arch_role_includes_arch_terms():
    """Architect/platform title → architecture leader role terms."""
    job = {"title": "Principal Platform Architect", "company": "Acme",
           "location": "Berlin, Germany", "jd_text": ""}
    s = boolean_search_string(job)
    assert '"Acme"' in s
    assert "head of architecture" in s
    assert '"Berlin"' in s


def test_boolean_search_string_city_appended():
    """City (before first comma) is appended when not remote/global."""
    job = {"title": "Cloud Architect", "company": "X", "location": "Copenhagen, Denmark"}
    s = boolean_search_string(job)
    assert '"Copenhagen"' in s


def test_boolean_search_string_remote_city_excluded():
    """Remote location: no city clause in the search string."""
    job = {"title": "AI Architect", "company": "X", "location": "Remote, Europe"}
    s = boolean_search_string(job)
    assert '"Remote"' not in s
    assert '"Europe"' not in s


def test_boolean_search_string_europe_city_excluded():
    job = {"title": "AI Architect", "company": "X", "location": "Europe (Remote)"}
    s = boolean_search_string(job)
    assert '"Europe' not in s


def test_boolean_search_string_default_domain():
    """Unrecognised title → generic hiring/engineering manager terms."""
    job = {"title": "Product Manager", "company": "Y", "location": "Stockholm, Sweden"}
    s = boolean_search_string(job)
    assert "engineering manager" in s or "hiring manager" in s


# ---------------------------------------------------------------------------
# LinkedIn play — public_contacts
# ---------------------------------------------------------------------------

def test_public_contacts_extracts_emails():
    contacts = public_contacts(JOB)
    email_vals = {c["value"] for c in contacts if c["type"] == "email"}
    assert "careers@epam.com" in email_vals
    assert "jane@epam.com" in email_vals


def test_public_contacts_extracts_named_contact():
    job = {"title": "AI Architect", "company": "X",
           "jd_text": "Please contact Jane Smith for more information about the role."}
    contacts = public_contacts(job)
    names = [c["value"] for c in contacts if c["type"] == "name"]
    assert "Jane Smith" in names


def test_public_contacts_named_contact_multiple_cues():
    jd = "Reach out to Alice Brown or email Bob Chen with questions."
    job = {"title": "Architect", "company": "Y", "jd_text": jd}
    contacts = public_contacts(job)
    names = {c["value"] for c in contacts if c["type"] == "name"}
    assert "Alice Brown" in names or "Bob Chen" in names  # at least one found


def test_public_contacts_always_has_search_hint():
    """A search_hint entry is always appended, even when the JD has no emails."""
    job = {"title": "AI Architect", "company": "X", "jd_text": "No emails here."}
    contacts = public_contacts(job)
    hints = [c for c in contacts if c["type"] == "search_hint"]
    assert len(hints) == 1
    assert hints[0]["value"]  # non-empty


def test_public_contacts_search_hint_ai_role():
    job = {"title": "Head of AI Engineering", "company": "X", "jd_text": ""}
    contacts = public_contacts(job)
    hint = next(c for c in contacts if c["type"] == "search_hint")
    assert "Head of AI" in hint["value"]


def test_public_contacts_search_hint_arch_role():
    job = {"title": "Principal Architect", "company": "X", "jd_text": ""}
    contacts = public_contacts(job)
    hint = next(c for c in contacts if c["type"] == "search_hint")
    assert "Head of Architecture" in hint["value"]


def test_public_contacts_no_duplicate_emails():
    jd = "Email us at info@co.com or info@co.com again."
    job = {"title": "AI Architect", "company": "X", "jd_text": jd}
    emails = [c for c in public_contacts(job) if c["type"] == "email"]
    assert len(emails) == 1


# ---------------------------------------------------------------------------
# LinkedIn play — build + render (manual-only)
# ---------------------------------------------------------------------------

def test_build_linkedin_play_truncates_note(profile):
    long_note = "x" * 500
    client = _fake_openai(Outreach(connection_note=long_note, follow_up="hello").model_dump_json())
    play = build_linkedin_play(profile, JOB, client=client)
    assert play["manual_only"] is True
    assert len(play["connection_note"]) <= 300
    assert play["search_string"]


def test_build_linkedin_play_threads_match_points_into_prompt(profile):
    """match_points kwarg must appear in the LLM system prompt."""
    captured_calls: list[dict] = []

    class _CapturingCC:
        def create(self, **kwargs):
            captured_calls.append(kwargs)
            out = Outreach(connection_note="hi", follow_up="follow up")
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=out.model_dump_json()))]
            )

    client = SimpleNamespace(chat=SimpleNamespace(completions=_CapturingCC()))
    build_linkedin_play(profile, JOB, client=client,
                        match_points=["Strong RAG expertise", "Azure AI Foundry experience"])
    assert captured_calls, "LLM was not called"
    system = captured_calls[0]["messages"][0]["content"]
    assert "Strong RAG expertise" in system


def test_render_play_email_is_manual_labelled():
    play = {"search_string": "X AND Y",
            "contacts": [{"value": "a@b.com", "source": "posting"}],
            "connection_note": "hi", "follow_up": "more"}
    body = render_play_email(JOB, play)
    assert "MANUALLY" in body.upper()
    assert "X AND Y" in body
    assert "a@b.com" in body


def test_render_play_email_displays_search_hint():
    play = {"search_string": "X AND Y",
            "contacts": [
                {"type": "email", "value": "a@b.com", "source": "posting"},
                {"type": "search_hint", "value": "Look for: Head of AI",
                 "source": "suggested — search on company pages / LinkedIn"},
            ],
            "connection_note": "hi", "follow_up": "more"}
    body = render_play_email(JOB, play)
    assert "Head of AI" in body
    assert "suggested" in body
