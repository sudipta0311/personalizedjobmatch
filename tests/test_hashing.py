"""Tests for content_hash and normalisation helpers."""

from agent.utils.hashing import content_hash, normalise_title, normalise_company


def test_hash_is_deterministic():
    h1 = content_hash("Acme Corp", "Senior AI Architect", "Amsterdam, NL")
    h2 = content_hash("Acme Corp", "Senior AI Architect", "Amsterdam, NL")
    assert h1 == h2


def test_hash_normalises_case():
    h1 = content_hash("ACME CORP", "SENIOR AI ARCHITECT", "AMSTERDAM, NL")
    h2 = content_hash("acme corp", "senior ai architect", "amsterdam, nl")
    assert h1 == h2


def test_hash_normalises_extra_whitespace():
    h1 = content_hash("Acme Corp", "Senior  AI  Architect", "Amsterdam")
    h2 = content_hash("Acme Corp", "Senior AI Architect", "Amsterdam")
    assert h1 == h2


def test_hash_differs_on_different_title():
    h1 = content_hash("Acme", "Senior AI Architect", "Amsterdam")
    h2 = content_hash("Acme", "Junior AI Architect", "Amsterdam")
    assert h1 != h2


def test_hash_differs_on_different_company():
    h1 = content_hash("Acme", "Senior AI Architect", "Amsterdam")
    h2 = content_hash("Beta Corp", "Senior AI Architect", "Amsterdam")
    assert h1 != h2


def test_hash_none_location():
    h1 = content_hash("Acme", "Senior AI Architect", None)
    h2 = content_hash("Acme", "Senior AI Architect", "")
    assert h1 == h2


def test_normalise_title_strips_punctuation():
    assert normalise_title("Senior AI-Architect (Remote)") == "senior aiarchitect remote"
