"""Tests for config loading and validation."""

import pytest
import yaml
import pathlib
import tempfile


def _write_profile(data: dict, path: pathlib.Path) -> None:
    with open(path, "w") as f:
        yaml.dump(data, f)


MINIMAL_PROFILE = {
    "personal": {"name": "Test User", "email": "test@example.com"},
    "seniority": {"level": "senior", "years_experience": 10},
    "target": {"titles": ["AI Architect"]},
    "market_framings": {"eu": {}, "india": {}, "gcc": {}},
    "work_authorisation": {"DK": "authorised", "default": "unknown"},
    "discovery": {"sources": {}, "search_keywords": []},
    "scoring": {"composite_weights": {}, "cutoff_score": 60, "top_n_digest": 10},
    "cv_content": {"summary": "Test summary", "experience": [], "skills": {}},
}


def test_load_profile_valid(tmp_path):
    from agent.config import load_profile
    p = tmp_path / "profile.yaml"
    _write_profile(MINIMAL_PROFILE, p)
    profile = load_profile(str(p))
    assert profile["personal"]["name"] == "Test User"


def test_load_profile_missing_file():
    from agent.config import load_profile
    with pytest.raises(FileNotFoundError):
        load_profile("/nonexistent/profile.yaml")


def test_load_profile_missing_key(tmp_path):
    from agent.config import load_profile
    bad = {k: v for k, v in MINIMAL_PROFILE.items() if k != "scoring"}
    p = tmp_path / "profile.yaml"
    _write_profile(bad, p)
    with pytest.raises(ValueError, match="scoring"):
        load_profile(str(p))


def test_load_profile_missing_personal_name(tmp_path):
    from agent.config import load_profile
    bad = dict(MINIMAL_PROFILE)
    bad["personal"] = {"email": "test@example.com"}  # missing name
    p = tmp_path / "profile.yaml"
    _write_profile(bad, p)
    with pytest.raises(ValueError, match="name"):
        load_profile(str(p))


def test_env_overrides_preferences_and_linkedin(tmp_path, monkeypatch):
    from agent.config import load_profile
    monkeypatch.setenv("JOB_PREFERENCES", "Senior AI Architect in EU")
    monkeypatch.setenv("LINKEDIN_URL", "https://linkedin.com/in/test")
    p = tmp_path / "profile.yaml"
    _write_profile(MINIMAL_PROFILE, p)
    profile = load_profile(str(p))
    assert profile["job_preferences"] == "Senior AI Architect in EU"
    assert profile["personal"]["linkedin_url"] == "https://linkedin.com/in/test"


def test_job_preferences_defaults_empty(tmp_path, monkeypatch):
    from agent.config import load_profile
    monkeypatch.delenv("JOB_PREFERENCES", raising=False)
    p = tmp_path / "profile.yaml"
    _write_profile(MINIMAL_PROFILE, p)
    profile = load_profile(str(p))
    assert profile["job_preferences"] == ""
