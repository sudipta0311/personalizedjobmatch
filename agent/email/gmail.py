"""Gmail API client — Phase 3.

Sends the weekly digest (and, in Phase 4, reads replies) using OAuth. The
refresh token + client id/secret come from GitHub Secrets via Settings; we never
do an interactive flow at runtime (that's the one-time scripts/gmail_auth.py).

Google libraries are imported lazily inside build_service() so the module can be
imported (and the pure MIME/helpers unit-tested) without them installed.
"""

from __future__ import annotations

import base64
import logging
import os
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any

from agent.config import Settings

logger = logging.getLogger(__name__)

_TOKEN_URI = "https://oauth2.googleapis.com/token"
_SCOPES = [
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.modify",
]


def build_raw_message(
    to: str,
    subject: str,
    text_body: str,
    html_body: str | None = None,
    *,
    sender: str = "me",
    thread_headers: dict[str, str] | None = None,
    attachments: list[str] | None = None,
) -> dict[str, Any]:
    """Build a Gmail API `messages.send` body (base64url-encoded MIME).

    Pure + dependency-free, so it's unit-testable without google libs.
    """
    if html_body:
        body: Any = MIMEMultipart("alternative")
        body.attach(MIMEText(text_body, "plain", "utf-8"))
        body.attach(MIMEText(html_body, "html", "utf-8"))
    else:
        body = MIMEText(text_body, "plain", "utf-8")

    if attachments:
        mime: Any = MIMEMultipart("mixed")
        mime.attach(body)
        for path in attachments:
            with open(path, "rb") as fh:
                part = MIMEApplication(fh.read())
            part.add_header(
                "Content-Disposition", "attachment", filename=os.path.basename(path)
            )
            mime.attach(part)
    else:
        mime = body

    mime["To"] = to
    mime["From"] = sender
    mime["Subject"] = subject
    for header, value in (thread_headers or {}).items():
        mime[header] = value

    raw = base64.urlsafe_b64encode(mime.as_bytes()).decode("ascii")
    return {"raw": raw}


def build_service(settings: Settings) -> Any:
    """Construct an authenticated Gmail API service from the refresh token."""
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    creds = Credentials(
        token=None,
        refresh_token=settings.gmail_refresh_token,
        client_id=settings.gmail_client_id,
        client_secret=settings.gmail_client_secret,
        token_uri=_TOKEN_URI,
        scopes=_SCOPES,
    )
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def send_message(
    service: Any,
    to: str,
    subject: str,
    text_body: str,
    html_body: str | None = None,
) -> tuple[str, str]:
    """Send one email; return (message_id, thread_id)."""
    body = build_raw_message(to, subject, text_body, html_body)
    sent = service.users().messages().send(userId="me", body=body).execute()
    msg_id = sent.get("id", "")
    thread_id = sent.get("threadId", "")
    logger.info("Sent digest email: message_id=%s thread_id=%s", msg_id, thread_id)
    return msg_id, thread_id


def send_digest(
    settings: Settings,
    subject: str,
    text_body: str,
    html_body: str | None = None,
) -> tuple[str, str]:
    """Convenience: build the service and send to the configured recipient."""
    service = build_service(settings)
    return send_message(service, settings.email_to, subject, text_body, html_body)


def send_reply(
    service: Any,
    to: str,
    subject: str,
    text_body: str,
    *,
    thread_id: str,
    in_reply_to: str | None = None,
) -> tuple[str, str]:
    """Send a plain-text reply inside an existing Gmail thread."""
    headers: dict[str, str] = {}
    if in_reply_to:
        headers["In-Reply-To"] = in_reply_to
        headers["References"] = in_reply_to
    body = build_raw_message(to, subject, text_body, thread_headers=headers)
    body["threadId"] = thread_id
    sent = service.users().messages().send(userId="me", body=body).execute()
    return sent.get("id", ""), sent.get("threadId", "")


def send_reply_with_attachments(
    service: Any,
    to: str,
    subject: str,
    text_body: str,
    attachments: list[str],
    *,
    thread_id: str,
    in_reply_to: str | None = None,
) -> tuple[str, str]:
    """Send a threaded reply carrying file attachments (e.g. CV + cover letter)."""
    headers: dict[str, str] = {}
    if in_reply_to:
        headers["In-Reply-To"] = in_reply_to
        headers["References"] = in_reply_to
    body = build_raw_message(
        to, subject, text_body, thread_headers=headers, attachments=attachments
    )
    body["threadId"] = thread_id
    sent = service.users().messages().send(userId="me", body=body).execute()
    return sent.get("id", ""), sent.get("threadId", "")


# ---------------------------------------------------------------------------
# Reading replies (Phase 4)
# ---------------------------------------------------------------------------

def get_thread(service: Any, thread_id: str) -> dict[str, Any]:
    """Fetch a full Gmail thread (all messages)."""
    return service.users().threads().get(
        userId="me", id=thread_id, format="full"
    ).execute()


def _header(payload: dict[str, Any], name: str) -> str:
    for h in payload.get("headers", []):
        if h.get("name", "").lower() == name.lower():
            return h.get("value", "")
    return ""


def extract_plain_text(message: dict[str, Any]) -> str:
    """Return the best-effort plain-text body of a Gmail message.

    Walks the MIME parts for text/plain; falls back to the message snippet.
    """
    payload = message.get("payload", {})

    def _walk(part: dict[str, Any]) -> str | None:
        if part.get("mimeType") == "text/plain":
            data = part.get("body", {}).get("data")
            if data:
                return base64.urlsafe_b64decode(data.encode()).decode(
                    "utf-8", errors="replace"
                )
        for sub in part.get("parts", []) or []:
            found = _walk(sub)
            if found is not None:
                return found
        return None

    return (_walk(payload) or message.get("snippet", "")).strip()


def _message_summary(msg: dict[str, Any], thread_id: str) -> dict[str, Any]:
    payload = msg.get("payload", {})
    return {
        "message_id": msg.get("id"),
        "thread_id": thread_id,
        "from": _header(payload, "From"),
        "subject": _header(payload, "Subject"),
        "body": extract_plain_text(msg),
    }


def list_thread_messages(service: Any, thread_id: str) -> list[dict[str, Any]]:
    """Return ALL messages in a thread as {message_id, thread_id, from, subject, body}.

    We don't filter on the UNREAD label: when the agent's Gmail account is also
    the recipient/replier (single-user setup), the user's reply is a 'sent'
    message and never gets UNREAD. The caller excludes the digest message itself
    and relies on command-parsing + the unique-message_id claim for idempotency.
    """
    thread = get_thread(service, thread_id)
    return [_message_summary(m, thread_id) for m in thread.get("messages", [])]


def list_unread_replies(service: Any, thread_id: str) -> list[dict[str, Any]]:
    """Return only UNREAD messages in a thread (multi-account setups)."""
    thread = get_thread(service, thread_id)
    return [
        _message_summary(m, thread_id)
        for m in thread.get("messages", [])
        if "UNREAD" in (m.get("labelIds") or [])
    ]


def mark_read(service: Any, message_id: str) -> None:
    """Remove the UNREAD label so a reply isn't picked up again."""
    service.users().messages().modify(
        userId="me", id=message_id, body={"removeLabelIds": ["UNREAD"]}
    ).execute()
