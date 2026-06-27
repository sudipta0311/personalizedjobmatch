"""Parse-verification of the generated CV — Phase 5.

Re-reads the generated .docx and asserts the load-bearing fields survived
rendering: name, contact, every role's title + company + start date, and a
sample of skills. This is the realistic "ATS maximisation" — verified
parseability, NOT a "100% ATS pass" claim.

Returns (ok, missing). If anything is missing, the caller flags it in the email
so the human can review rather than the agent silently shipping a broken CV.
"""

from __future__ import annotations

from typing import Any

from docx import Document


def _extract_text(path: str) -> str:
    doc = Document(path)
    return "\n".join(p.text for p in doc.paragraphs)


def verify_cv(path: str, profile: dict[str, Any]) -> tuple[bool, list[str]]:
    """Assert key profile fields are recoverable from the rendered CV."""
    text = _extract_text(path)
    low = text.lower()
    missing: list[str] = []

    personal = profile.get("personal", {})
    cv = profile.get("cv_content", {})

    def _check(label: str, value: str | None) -> None:
        if value and value.lower() not in low:
            missing.append(label)

    _check("name", personal.get("name"))
    _check("email", personal.get("email"))
    _check("phone", personal.get("phone"))

    for e in cv.get("experience", []):
        _check(f"role title '{e.get('title')}'", e.get("title"))
        _check(f"company '{e.get('company')}'", e.get("company"))
        if e.get("start"):
            _check(f"start date for '{e.get('title')}'", e.get("start"))

    # a sample of skills (first of each group) should be present
    for group, items in (cv.get("skills") or {}).items():
        if items:
            _check(f"skill '{items[0]}'", items[0])

    return (len(missing) == 0, missing)
