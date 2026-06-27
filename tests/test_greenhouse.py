"""Tests for the Greenhouse discovery source.

Uses the `responses` library to mock HTTP calls — no real network needed.
"""

from __future__ import annotations

import json
from datetime import date

import pytest
import responses as resp_lib

from agent.discovery.greenhouse import GreenhouseSource, _strip_html, _parse_country, _parse_date


# ---------------------------------------------------------------------------
# Unit tests for helpers
# ---------------------------------------------------------------------------

def test_strip_html_basic():
    assert _strip_html("<p>Hello <b>world</b></p>") == "Hello world"


def test_strip_html_entities():
    assert _strip_html("&lt;code&gt;") == "<code>"


def test_strip_html_none():
    assert _strip_html(None) is None


def test_strip_html_empty():
    assert _strip_html("") is None


def test_parse_country_amsterdam():
    assert _parse_country("Amsterdam, Netherlands") == "NL"


def test_parse_country_london():
    assert _parse_country("London, UK") == "GB"


def test_parse_country_dubai():
    assert _parse_country("Dubai, UAE") == "AE"


def test_parse_country_bangalore():
    assert _parse_country("Bangalore, India") == "IN"


def test_parse_country_unknown():
    assert _parse_country("Atlantis") is None


def test_parse_country_none():
    assert _parse_country(None) is None


def test_parse_date_iso():
    result = _parse_date("2024-03-15T10:00:00Z")
    assert result == date(2024, 3, 15)


def test_parse_date_none():
    assert _parse_date(None) is None


# ---------------------------------------------------------------------------
# Integration-style test with mocked HTTP
# ---------------------------------------------------------------------------

MOCK_GREENHOUSE_RESPONSE = {
    "jobs": [
        {
            "id": 123456,
            "title": "Senior AI Architect",
            "location": {"name": "Amsterdam, Netherlands"},
            "absolute_url": "https://boards.greenhouse.io/acme/jobs/123456",
            "content": "<p>We are looking for a <b>Senior AI Architect</b> with LangChain experience.</p>",
            "updated_at": "2024-03-15T10:00:00Z",
        },
        {
            "id": 999999,
            "title": "Junior Developer",  # should be excluded by keyword filter
            "location": {"name": "Berlin, Germany"},
            "absolute_url": "https://boards.greenhouse.io/acme/jobs/999999",
            "content": "<p>Entry-level role.</p>",
            "updated_at": "2024-03-14T09:00:00Z",
        },
    ],
    "name": "Acme Corp",
}


@resp_lib.activate
def test_greenhouse_fetch_filters_by_keyword():
    resp_lib.add(
        resp_lib.GET,
        "https://boards-api.greenhouse.io/v1/boards/acme/jobs",
        json=MOCK_GREENHOUSE_RESPONSE,
        status=200,
    )

    source = GreenhouseSource()
    config = {"boards": ["acme"], "fetch_all": True}
    keywords = ["AI Architect", "LangChain"]

    postings = source.fetch(config, keywords)

    assert len(postings) == 1
    assert postings[0].title == "Senior AI Architect"
    assert postings[0].company == "Acme Corp"
    assert postings[0].country == "NL"
    assert postings[0].location == "Amsterdam, Netherlands"
    assert "Senior AI Architect" in (postings[0].jd_text or "")


@resp_lib.activate
def test_greenhouse_fetch_no_boards():
    source = GreenhouseSource()
    postings = source.fetch({"boards": []}, ["AI"])
    assert postings == []


@resp_lib.activate
def test_greenhouse_handles_http_error():
    resp_lib.add(
        resp_lib.GET,
        "https://boards-api.greenhouse.io/v1/boards/badboard/jobs",
        status=404,
    )

    source = GreenhouseSource()
    # Should not raise — logs error and returns empty
    postings = source.fetch({"boards": ["badboard"]}, ["AI"])
    assert postings == []
