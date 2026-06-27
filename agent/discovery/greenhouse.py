"""Greenhouse ATS discovery source.

Uses the Greenhouse public boards API — no authentication required.
Endpoint: GET https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs?content=true

The ?content=true parameter includes the full job description HTML in the response.
We strip HTML tags to extract plain text for scoring.

Rate limiting: we add a small sleep between board requests to be a polite client.
"""

from __future__ import annotations

import html
import logging
import re
import time
from datetime import date, datetime
from typing import Any

import requests

from agent.discovery.base import BaseSource, JobPosting

logger = logging.getLogger(__name__)

_BASE_URL = "https://boards-api.greenhouse.io/v1/boards"
_REQUEST_DELAY_SECONDS = 1.0   # polite delay between board requests


def _strip_html(raw: str | None) -> str | None:
    if not raw:
        return None
    # Strip real tags FIRST, then unescape entities — otherwise entity-encoded
    # angle brackets (e.g. "&lt;code&gt;") would decode to "<code>" and then be
    # stripped as if they were markup.
    text = re.sub(r"<[^>]+>", " ", raw)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text or None


def _parse_country(location: str | None) -> str | None:
    """Best-effort extract a 2-letter ISO country code from a location string."""
    if not location:
        return None

    loc = location.lower()

    _COUNTRY_MAP = {
        # EU / EEA
        "netherlands": "NL", "amsterdam": "NL", "rotterdam": "NL", "eindhoven": "NL",
        "germany": "DE", "berlin": "DE", "munich": "DE", "hamburg": "DE", "frankfurt": "DE",
        "denmark": "DK", "copenhagen": "DK",
        "sweden": "SE", "stockholm": "SE",
        "finland": "FI", "helsinki": "FI",
        "belgium": "BE", "brussels": "BE",
        "france": "FR", "paris": "FR",
        "norway": "NO", "oslo": "NO",
        "austria": "AT", "vienna": "AT",
        "switzerland": "CH", "zurich": "CH", "geneva": "CH",
        "spain": "ES", "madrid": "ES", "barcelona": "ES",
        "portugal": "PT", "lisbon": "PT",
        "ireland": "IE", "dublin": "IE",
        "poland": "PL", "warsaw": "PL",
        # UK
        "united kingdom": "GB", "uk": "GB", "london": "GB", "england": "GB",
        "scotland": "GB", "manchester": "GB",
        # Asia
        "india": "IN", "bangalore": "IN", "bengaluru": "IN", "mumbai": "IN",
        "delhi": "IN", "hyderabad": "IN", "pune": "IN", "chennai": "IN",
        "singapore": "SG",
        # GCC
        "united arab emirates": "AE", "uae": "AE", "dubai": "AE", "abu dhabi": "AE",
        "saudi arabia": "SA", "riyadh": "SA", "jeddah": "SA",
        "qatar": "QA", "doha": "QA",
        "kuwait": "KW",
        "bahrain": "BH", "manama": "BH",
        "oman": "OM", "muscat": "OM",
        # Americas
        "united states": "US", "usa": "US", "new york": "US",
        "san francisco": "US", "seattle": "US", "boston": "US",
        "canada": "CA", "toronto": "CA", "vancouver": "CA",
    }

    for keyword, code in _COUNTRY_MAP.items():
        if keyword in loc:
            return code

    return None


def _parse_date(date_str: str | None) -> date | None:
    if not date_str:
        return None
    # Normalise Z suffix so %z can parse it (Python 3.11+ handles Z natively,
    # but the replace keeps us compatible with 3.10 too).
    normalised = date_str.strip().replace("Z", "+00:00")
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(normalised[:25], fmt).date()
        except ValueError:
            continue
    return None


def _matches_keywords(title: str, jd_text: str | None, keywords: list[str]) -> bool:
    """Return True if any keyword appears in the title or JD text."""
    if not keywords:
        return True
    combined = (title + " " + (jd_text or "")).lower()
    return any(kw.lower() in combined for kw in keywords)


class GreenhouseSource(BaseSource):
    name = "greenhouse"

    def fetch(
        self,
        config: dict[str, Any],
        keywords: list[str],
    ) -> list[JobPosting]:
        boards: list[str] = config.get("boards", [])
        if not boards:
            logger.warning("Greenhouse: no board slugs configured in profile.yaml — skipping")
            return []

        results: list[JobPosting] = []

        session = requests.Session()
        session.headers["User-Agent"] = "job-search-agent/1.0 (private use)"

        for board_token in boards:
            try:
                postings = self._fetch_board(session, board_token, keywords)
                logger.info(
                    "Greenhouse board %r: fetched %d matching postings",
                    board_token,
                    len(postings),
                )
                results.extend(postings)
            except Exception as exc:
                logger.error(
                    "Greenhouse board %r: fetch failed — %s", board_token, exc
                )
            finally:
                time.sleep(_REQUEST_DELAY_SECONDS)

        return results

    def _fetch_board(
        self,
        session: requests.Session,
        board_token: str,
        keywords: list[str],
    ) -> list[JobPosting]:
        url = f"{_BASE_URL}/{board_token}/jobs"
        resp = session.get(url, params={"content": "true"}, timeout=30)
        resp.raise_for_status()
        data = resp.json()

        jobs_raw: list[dict] = data.get("jobs", [])
        company_name = data.get("name") or board_token

        postings: list[JobPosting] = []
        for job in jobs_raw:
            title = job.get("title", "")
            location_data = job.get("location") or {}
            location_name = location_data.get("name") or ""
            jd_text = _strip_html(job.get("content"))

            if not _matches_keywords(title, jd_text, keywords):
                continue

            country = _parse_country(location_name)
            posted = _parse_date(job.get("updated_at") or job.get("created_at"))

            postings.append(
                JobPosting(
                    source="greenhouse",
                    url=job.get("absolute_url", f"https://job-boards.greenhouse.io/{board_token}/jobs/{job.get('id')}"),
                    company=company_name,
                    title=title,
                    location=location_name or None,
                    country=country,
                    jd_text=jd_text,
                    posted_date=posted,
                    raw=job,
                )
            )

        return postings
