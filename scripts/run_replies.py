"""Entry point for the reply-poller workflow (GitHub Actions replies.yml).

Reads unread replies to recent digests, parses commands, executes them
(prepare/warm/info/skip/ask), emails an acknowledgement, and marks each reply
processed. Idempotent — safe to run on the 8-hourly schedule.

Usage:
    python scripts/run_replies.py
"""

from __future__ import annotations

import logging
import os
import sys

# Ensure the project root is importable whether run as `python scripts/run_replies.py`
# or `python -m scripts.run_replies` (GitHub Actions uses the former).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

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
    from agent.graphs.reply_graph import run_replies

    Settings.from_env()        # fail fast if secrets are missing
    apply_schema()
    logger.info("Starting reply-poller workflow")

    result = run_replies()

    n = len(result.get("execution_results", []))
    logger.info("Reply poller complete: processed %d new reply message(s)", n)

    if result.get("errors"):
        for err in result["errors"]:
            logger.error("Error: %s", err)
        sys.exit(1)


if __name__ == "__main__":
    main()
