"""Entry point for the weekly discovery workflow (GitHub Actions discover.yml).

Usage:
    python -m scripts.run_discover
    # or
    python scripts/run_discover.py
"""

from __future__ import annotations

import logging
import sys

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger(__name__)


def main() -> None:
    from agent.config import Settings
    from agent.db.client import apply_schema
    from agent.graphs.discover_graph import run_discover

    settings = Settings.from_env()
    logger.info("Starting discovery workflow")

    apply_schema()
    logger.info("DB schema verified")

    result = run_discover()

    n_raw = len(result.get("raw_postings", []))
    n_new = len(result.get("new_postings", []))
    n_persisted = len(result.get("persisted_jobs", []))
    n_filtered = len(result.get("filtered_jobs", []))

    logger.info(
        "Discovery complete: %d fetched → %d new → %d persisted → %d in digest",
        n_raw, n_new, n_persisted, n_filtered,
    )

    if result.get("errors"):
        for err in result["errors"]:
            logger.error("Error: %s", err)
        sys.exit(1)


if __name__ == "__main__":
    main()
