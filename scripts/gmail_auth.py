"""One-time helper: exchange Gmail OAuth credentials for a refresh token.

Run this ONCE locally (not on GitHub Actions) to generate the refresh token
that you then store as the GMAIL_REFRESH_TOKEN GitHub Secret.

Prerequisites:
1. Enable Gmail API in Google Cloud Console for your project.
2. Create OAuth 2.0 credentials (Desktop application type).
3. Download credentials.json and place it in the project root.
4. Run:  python scripts/gmail_auth.py
5. A browser window opens — authorise access.
6. Copy the printed refresh token into your GitHub Secret GMAIL_REFRESH_TOKEN.

NEVER commit credentials.json or the generated token to git.
"""

from __future__ import annotations

import json
import os

SCOPES = [
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.modify",
]


def main() -> None:
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError:
        print("Run: pip install google-auth-oauthlib")
        return

    credentials_path = os.path.join(os.path.dirname(__file__), "..", "credentials.json")
    if not os.path.exists(credentials_path):
        print("credentials.json not found. Download it from Google Cloud Console.")
        return

    flow = InstalledAppFlow.from_client_secrets_file(credentials_path, SCOPES)
    creds = flow.run_local_server(port=0)

    print("\n--- Copy the following value into your GitHub Secret: GMAIL_REFRESH_TOKEN ---")
    print(creds.refresh_token)
    print("--- Also set GMAIL_CLIENT_ID and GMAIL_CLIENT_SECRET from credentials.json ---")
    with open(credentials_path) as f:
        raw = json.load(f)
    installed = raw.get("installed") or raw.get("web", {})
    print(f"GMAIL_CLIENT_ID:     {installed.get('client_id')}")
    print(f"GMAIL_CLIENT_SECRET: {installed.get('client_secret')}")


if __name__ == "__main__":
    main()
