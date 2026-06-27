"""Content hashing for deduplication.

The canonical dedupe key is SHA-256(company + normalised_title + location).
This is deterministic and cheap to compute from any source.
"""

from __future__ import annotations

import hashlib
import re


def normalise_title(title: str) -> str:
    """Lowercase, collapse whitespace, strip punctuation for consistent hashing."""
    t = title.lower().strip()
    t = re.sub(r"[^\w\s]", "", t)
    t = re.sub(r"\s+", " ", t)
    return t


def normalise_company(company: str) -> str:
    return company.lower().strip()


def normalise_location(location: str | None) -> str:
    if not location:
        return ""
    return location.lower().strip()


def content_hash(company: str, title: str, location: str | None = None) -> str:
    """Return a hex SHA-256 dedupe hash for a job posting."""
    key = "|".join([
        normalise_company(company),
        normalise_title(title),
        normalise_location(location),
    ])
    return hashlib.sha256(key.encode()).hexdigest()
