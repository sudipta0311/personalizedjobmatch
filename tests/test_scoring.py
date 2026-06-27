"""Tests for Phase 2 scoring — rule gates, composite blend, and filtering.

The LLM is never called for real: score_job/score_jobs take an injectable client,
and these tests pass a fake whose messages.parse returns a canned LLMFit.
"""

from __future__ import annotations

import pytest

from agent.scoring import rules
from agent.scoring.filter import filter_jobs
from agent.scoring.llm_fit import LLMFit, score_fit
from agent.scoring.score import ScoredJob, composite_score, score_job, score_jobs


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def profile() -> dict:
    return {
        "personal": {"name": "Test User"},
        "seniority": {
            "level": "senior",
            "years_experience": 15,
            "min_years_in_jd": 8,
            "max_years_in_jd": 20,
        },
        "target": {
            "exclude_title_substrings": ["Junior", "Intern", "VP of Engineering"],
            "exclude_companies": ["BadCorp"],
            "exclude_domains": ["gambling"],
        },
        "work_authorisation": {
            "DK": "authorised",
            "NL": "blue_card_eligible",
            "GB": "needs_licensed_sponsor",
            "US": "not_authorised",
            "default": "unknown",
        },
        "scoring": {
            "composite_weights": {"llm_fit": 0.60, "auth_fit": 0.20, "grade_fit": 0.20},
            "cutoff_score": 60,
            "top_n_digest": 3,
            "hard_veto_on_auth": ["not_authorised"],
        },
        "cv_content": {"summary": "s", "skills": {}, "certifications": []},
        "models": {"scoring": "claude-sonnet-4-6"},
    }


class FakeParseResponse:
    def __init__(self, fit: LLMFit):
        self.parsed_output = fit


class FakeClient:
    """Stands in for anthropic.Anthropic — records calls, returns a canned fit."""

    def __init__(self, fit: LLMFit):
        self._fit = fit
        self.calls: list[dict] = []

        outer = self

        class _Messages:
            @staticmethod
            def parse(**kwargs):
                outer.calls.append(kwargs)
                return FakeParseResponse(outer._fit)

        self.messages = _Messages()


def _fit(score: int = 85) -> LLMFit:
    return LLMFit(
        fit_score=score,
        match_points=["a", "b", "c"],
        gaps=["g"],
        rationale="solid fit",
    )


# ---------------------------------------------------------------------------
# Market tagging
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("country,expected", [
    ("NL", "eu"), ("DK", "eu"), ("GB", "eu"), ("CH", "eu"),
    ("IN", "india"),
    ("AE", "gcc"), ("SA", "gcc"), ("QA", "gcc"),
    ("US", "other"), ("BR", "other"),
    (None, "other"),
])
def test_market_tag(country, expected):
    assert rules.market_tag(country) == expected


# ---------------------------------------------------------------------------
# Auth gate
# ---------------------------------------------------------------------------

def test_auth_gate_authorised(profile):
    status, fit, veto, flag = rules.auth_gate(
        "DK", profile["work_authorisation"], profile["scoring"]["hard_veto_on_auth"]
    )
    assert status == "authorised"
    assert fit == 100.0
    assert veto is False


def test_auth_gate_blue_card(profile):
    status, fit, veto, flag = rules.auth_gate(
        "NL", profile["work_authorisation"], profile["scoring"]["hard_veto_on_auth"]
    )
    assert status == "blue_card_eligible"
    assert fit == 85.0
    assert "Blue-Card" in flag


def test_auth_gate_uk_sponsor_flagged(profile):
    status, fit, veto, flag = rules.auth_gate(
        "GB", profile["work_authorisation"], profile["scoring"]["hard_veto_on_auth"]
    )
    assert status == "needs_licensed_sponsor"
    assert veto is False
    assert "sponsor" in flag


def test_auth_gate_us_vetoed(profile):
    status, fit, veto, flag = rules.auth_gate(
        "US", profile["work_authorisation"], profile["scoring"]["hard_veto_on_auth"]
    )
    assert status == "not_authorised"
    assert veto is True
    assert fit == 0.0


def test_auth_gate_unknown_country(profile):
    status, fit, veto, flag = rules.auth_gate(
        "BR", profile["work_authorisation"], profile["scoring"]["hard_veto_on_auth"]
    )
    assert status == "unknown"
    assert fit == 50.0
    assert veto is False


# ---------------------------------------------------------------------------
# Grade gate
# ---------------------------------------------------------------------------

def test_parse_required_years_takes_highest():
    assert rules.parse_required_years("3 years of X and 10+ years of Y") == 10


def test_parse_required_years_none():
    assert rules.parse_required_years("No experience requirement listed") is None


def test_grade_gate_in_range(profile):
    fit, flag, req = rules.grade_gate("Requires 10+ years experience", profile["seniority"])
    assert fit == 100.0
    assert flag is None
    assert req == 10


def test_grade_gate_overqualified(profile):
    fit, flag, req = rules.grade_gate("Requires 3 years experience", profile["seniority"])
    assert fit == 60.0
    assert "overqualified" in flag


def test_grade_gate_stretch(profile):
    fit, flag, req = rules.grade_gate("Requires 25 years experience", profile["seniority"])
    assert fit == 50.0
    assert "stretch" in flag


def test_grade_gate_no_years_neutral(profile):
    fit, flag, req = rules.grade_gate("Senior architect role", profile["seniority"])
    assert fit == 80.0
    assert req is None


# ---------------------------------------------------------------------------
# Exclusions
# ---------------------------------------------------------------------------

def test_title_excluded(profile):
    assert rules.title_excluded("Junior AI Engineer", profile["target"]) == "Junior"
    assert rules.title_excluded("Senior AI Architect", profile["target"]) is None


def test_company_excluded(profile):
    assert rules.company_excluded("BadCorp Ltd", profile["target"]) == "BadCorp"


def test_domain_excluded(profile):
    assert rules.domain_excluded("online gambling platform", profile["target"]) == "gambling"


def test_apply_rules_excluded_title_vetoes(profile):
    job = {"title": "Junior Dev", "company": "Acme", "country": "DK", "jd_text": "10 years"}
    res = rules.apply_rules(job, profile)
    assert res.veto is True
    assert "title" in res.veto_reason


def test_apply_rules_clean(profile):
    job = {"title": "Senior AI Architect", "company": "Acme",
           "country": "NL", "jd_text": "10+ years required"}
    res = rules.apply_rules(job, profile)
    assert res.veto is False
    assert res.market_tag == "eu"
    assert res.auth_fit == 85.0
    assert res.grade_fit == 100.0


# ---------------------------------------------------------------------------
# Composite
# ---------------------------------------------------------------------------

def test_composite_score_blend():
    # weights sum to 1.0 already
    val = composite_score(90, 100, 100, (0.6, 0.2, 0.2))
    assert val == pytest.approx(0.6 * 90 + 0.2 * 100 + 0.2 * 100)


def test_composite_score_normalises_weights():
    # weights that don't sum to 1 are normalised
    val = composite_score(80, 80, 80, (3, 1, 1))
    assert val == pytest.approx(80.0)


def test_composite_score_zero_weights():
    assert composite_score(90, 90, 90, (0, 0, 0)) == 0.0


# ---------------------------------------------------------------------------
# score_job — integration of rules + (mocked) LLM
# ---------------------------------------------------------------------------

def test_score_job_clean_uses_llm(profile):
    client = FakeClient(_fit(90))
    job = {"id": "j1", "title": "Senior AI Architect", "company": "Acme",
           "country": "NL", "location": "Amsterdam", "jd_text": "10+ years required"}
    scored = score_job(job, profile, client=client)

    assert len(client.calls) == 1                  # LLM was called
    assert scored.llm_fit == 90.0
    assert scored.rule.market_tag == "eu"
    expected = 0.6 * 90 + 0.2 * 85 + 0.2 * 100
    assert scored.composite == pytest.approx(expected)
    assert scored.match_points == ["a", "b", "c"]


def test_score_job_vetoed_skips_llm(profile):
    client = FakeClient(_fit(99))
    job = {"id": "j2", "title": "Senior AI Architect", "company": "Acme",
           "country": "US", "jd_text": "10+ years"}   # US => not_authorised => veto
    scored = score_job(job, profile, client=client)

    assert client.calls == []                      # LLM NOT called for vetoed role
    assert scored.composite == 0.0
    assert scored.rule.veto is True


def test_score_row_shape(profile):
    client = FakeClient(_fit(70))
    job = {"id": "j3", "title": "AI Architect", "company": "Acme",
           "country": "DK", "jd_text": "10 years"}
    scored = score_job(job, profile, client=client)
    row = scored.score_row()
    assert row["job_id"] == "j3"
    assert row["llm_fit"] == 70.0
    assert "market_tag" in row["rule_flags"]
    assert isinstance(row["match_points"], list)


# ---------------------------------------------------------------------------
# llm_fit fallback
# ---------------------------------------------------------------------------

def test_score_fit_degrades_on_error(profile):
    class BoomClient:
        class messages:
            @staticmethod
            def parse(**kwargs):
                raise RuntimeError("api down")

    fit = score_fit({"title": "X", "company": "Y", "jd_text": "z"},
                    profile, client=BoomClient())
    assert fit.fit_score == 50
    assert "unavailable" in fit.gaps[0]


# ---------------------------------------------------------------------------
# Filtering
# ---------------------------------------------------------------------------

def _scored(profile, composite, veto=False):
    job = {"id": f"j{composite}", "title": "t", "company": "c", "country": "DK"}
    rule = rules.apply_rules({**job, "jd_text": "10 years"}, profile)
    rule.veto = veto
    return ScoredJob(
        job=job, rule=rule, llm=_fit(),
        auth_fit=100, grade_fit=100, llm_fit=composite,
        composite=composite, rationale="r",
        match_points=["a"], gaps=["g"],
    )


def test_filter_applies_cutoff_and_topn(profile):
    scored = [
        _scored(profile, 95),
        _scored(profile, 80),
        _scored(profile, 70),
        _scored(profile, 50),   # below cutoff 60
    ]
    out = filter_jobs(scored, profile)        # top_n_digest = 3
    assert [s.composite for s in out] == [95, 80, 70]


def test_filter_drops_vetoed(profile):
    scored = [
        _scored(profile, 99, veto=True),
        _scored(profile, 65),
    ]
    out = filter_jobs(scored, profile)
    assert [s.composite for s in out] == [65]


def test_filter_top_n_override(profile):
    scored = [_scored(profile, 95), _scored(profile, 90), _scored(profile, 85)]
    out = filter_jobs(scored, profile, top_n_override=1)
    assert len(out) == 1
    assert out[0].composite == 95


def test_score_jobs_batches(profile):
    client = FakeClient(_fit(88))
    jobs = [
        {"id": "a", "title": "Senior AI Architect", "company": "Acme",
         "country": "NL", "jd_text": "10+ years"},
        {"id": "b", "title": "Junior Dev", "company": "Acme",
         "country": "DK", "jd_text": "2 years"},   # vetoed by title
    ]
    out = score_jobs(jobs, profile, client=client)
    assert len(out) == 2
    assert len(client.calls) == 1                # only the non-vetoed role hit the LLM
