"""Abstract base class for job discovery sources.

All sources must return a list of JobPosting dicts normalised to the common schema.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date
from typing import Any


@dataclass
class JobPosting:
    """Normalised job posting — common schema across all sources."""

    source: str              # 'greenhouse' | 'lever' | 'career_page'
    url: str
    company: str
    title: str
    location: str | None
    country: str | None      # ISO 3166-1 alpha-2, best-effort
    jd_text: str | None
    posted_date: date | None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    def to_db_dict(self) -> dict[str, Any]:
        from agent.utils.hashing import content_hash
        return {
            "source": self.source,
            "url": self.url,
            "company": self.company,
            "title": self.title,
            "location": self.location,
            "country": self.country,
            "jd_text": self.jd_text,
            "posted_date": self.posted_date.isoformat() if self.posted_date else None,
            "content_hash": content_hash(self.company, self.title, self.location),
        }


class BaseSource(ABC):
    """Abstract discovery source."""

    name: str = "base"

    @abstractmethod
    def fetch(self, config: dict[str, Any], keywords: list[str]) -> list[JobPosting]:
        """Fetch and return normalised job postings.

        Args:
            config:   source-specific config block from profile.yaml discovery.sources.<name>
            keywords: title/description filter keywords from profile.yaml discovery.search_keywords
        """
        ...
