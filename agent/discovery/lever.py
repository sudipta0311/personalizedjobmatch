"""Lever ATS discovery source — stub, enabled in Phase 2+.

Lever public postings API: GET https://api.lever.co/v0/postings/<site>
"""

from __future__ import annotations

import logging
from typing import Any

from agent.discovery.base import BaseSource, JobPosting

logger = logging.getLogger(__name__)


class LeverSource(BaseSource):
    name = "lever"

    def fetch(
        self,
        config: dict[str, Any],
        keywords: list[str],
    ) -> list[JobPosting]:
        logger.info("Lever source is not yet enabled — skipping")
        return []
