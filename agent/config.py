"""Config loader — reads profile.yaml and merges env overrides.

Usage:
    from agent.config import load_profile, Settings

    profile = load_profile()              # from profile.yaml
    settings = Settings.from_env()        # from env / .env file
"""

from __future__ import annotations

import os
import pathlib
from dataclasses import dataclass, field
from typing import Any

import yaml
from dotenv import load_dotenv

load_dotenv()  # loads .env if present; no-op when env vars already set

_PROFILE_PATH = pathlib.Path(__file__).parent.parent / "profile.yaml"


def load_profile(path: str | pathlib.Path | None = None) -> dict[str, Any]:
    """Return the parsed profile.yaml as a plain dict."""
    target = pathlib.Path(path) if path else _PROFILE_PATH
    if not target.exists():
        raise FileNotFoundError(
            f"profile.yaml not found at {target}. "
            "Copy and fill in the template before running the agent."
        )
    with target.open() as fh:
        data = yaml.safe_load(fh)
    _validate_profile(data)
    return data


def _validate_profile(data: dict[str, Any]) -> None:
    required_top_keys = [
        "personal",
        "seniority",
        "target",
        "market_framings",
        "work_authorisation",
        "discovery",
        "scoring",
        "cv_content",
    ]
    missing = [k for k in required_top_keys if k not in data]
    if missing:
        raise ValueError(
            f"profile.yaml is missing required top-level keys: {missing}"
        )

    personal = data.get("personal", {})
    for key in ("name", "email"):
        if not personal.get(key):
            raise ValueError(f"profile.yaml: personal.{key} must be set")


@dataclass
class Settings:
    """Runtime settings sourced from environment variables."""

    anthropic_api_key: str
    neon_database_url: str
    gmail_client_id: str
    gmail_client_secret: str
    gmail_refresh_token: str
    email_to: str
    log_level: str = "INFO"
    digest_top_n_override: int | None = None

    @classmethod
    def from_env(cls) -> "Settings":
        def _require(name: str) -> str:
            val = os.environ.get(name)
            if not val:
                raise RuntimeError(
                    f"Required environment variable {name!r} is not set. "
                    "See .env.example for setup instructions."
                )
            return val

        top_n_raw = os.environ.get("DIGEST_TOP_N", "")
        top_n = int(top_n_raw) if top_n_raw.strip().isdigit() else None

        return cls(
            anthropic_api_key=_require("ANTHROPIC_API_KEY"),
            neon_database_url=_require("NEON_DATABASE_URL"),
            gmail_client_id=_require("GMAIL_CLIENT_ID"),
            gmail_client_secret=_require("GMAIL_CLIENT_SECRET"),
            gmail_refresh_token=_require("GMAIL_REFRESH_TOKEN"),
            email_to=_require("EMAIL_TO"),
            log_level=os.environ.get("LOG_LEVEL", "INFO"),
            digest_top_n_override=top_n,
        )
