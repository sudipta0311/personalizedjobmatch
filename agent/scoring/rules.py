"""Deterministic rule gates — Phase 2.

These run BEFORE the LLM and encode the user's hard, non-negotiable filters:

  * work-authorisation fit  (country -> auth status from profile)
  * seniority-grade fit     (years parsed from the JD vs the user's level)
  * market tag              (eu / india / gcc / other -> selects CV framing)
  * exclusions              (title / company / domain -> hard veto)

Everything here is a PURE FUNCTION of (job, profile) — no DB, no network — so the
encoded judgment is fully unit-testable. The LLM only sees roles these gates let
through (a hard veto removes a role from the digest entirely).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Market tagging — which CV framing a role should use
# ---------------------------------------------------------------------------

# EU/EEA + UK + Switzerland all use the "eu" framing. Auth (sponsorship) is a
# SEPARATE concern handled by auth_gate — framing is about CV positioning only.
_EU_COUNTRIES = {
    "NL", "DE", "DK", "SE", "FI", "BE", "FR", "NO", "AT", "CH",
    "ES", "PT", "IE", "PL", "GB", "IT", "CZ", "LU",
}
_GCC_COUNTRIES = {"AE", "SA", "QA", "BH", "KW", "OM"}


def market_tag(country: str | None) -> str:
    """Map an ISO country code to a market framing tag: eu | india | gcc | other."""
    if not country:
        return "other"
    code = country.upper()
    if code == "IN":
        return "india"
    if code in _GCC_COUNTRIES:
        return "gcc"
    if code in _EU_COUNTRIES:
        return "eu"
    return "other"


# ---------------------------------------------------------------------------
# Work-authorisation gate
# ---------------------------------------------------------------------------

# Numeric component (0-100) for each authorisation status.
_AUTH_FIT = {
    "authorised": 100.0,
    "citizen": 100.0,
    "blue_card_eligible": 85.0,
    "needs_visa": 55.0,
    "needs_licensed_sponsor": 35.0,   # always flagged — needs a licensed sponsor
    "not_authorised": 0.0,
    "unknown": 50.0,
}


def auth_status(country: str | None, work_auth: dict[str, str]) -> str:
    """Resolve the user's work-auth status for a role's country."""
    if not country:
        return work_auth.get("default", "unknown")
    return work_auth.get(country.upper(), work_auth.get("default", "unknown"))


def auth_gate(
    country: str | None,
    work_auth: dict[str, str],
    hard_veto_statuses: list[str],
) -> tuple[str, float, bool, str | None]:
    """Return (status, auth_fit 0-100, veto, flag).

    `veto` is True when the status is in the profile's hard_veto list (role hidden).
    `flag` is a short human-readable note for the digest, or None.
    """
    status = auth_status(country, work_auth)
    fit = _AUTH_FIT.get(status, 50.0)
    veto = status in (hard_veto_statuses or [])

    flag: str | None = None
    if status == "blue_card_eligible":
        flag = "Blue-Card / HSM eligible"
    elif status == "needs_licensed_sponsor":
        flag = "needs a licensed visa sponsor"
    elif status == "needs_visa":
        flag = "employer visa sponsorship required"
    elif status == "not_authorised":
        flag = "not currently work-authorised"
    elif status == "unknown":
        flag = "work-auth unknown — verify"

    return status, fit, veto, flag


# ---------------------------------------------------------------------------
# Seniority-grade gate
# ---------------------------------------------------------------------------

# Matches "8+ years", "minimum 10 years", "5 yrs", etc. Captures the integer.
_YEARS_RE = re.compile(r"(\d{1,2})\s*\+?\s*(?:years|yrs|year)\b", re.IGNORECASE)


def parse_required_years(jd_text: str | None) -> int | None:
    """Best-effort: the highest 'N years' figure mentioned in the JD (the bar)."""
    if not jd_text:
        return None
    matches = [int(m) for m in _YEARS_RE.findall(jd_text)]
    matches = [m for m in matches if 0 < m <= 40]   # drop noise (e.g. "2024 years")
    return max(matches) if matches else None


def grade_gate(
    jd_text: str | None,
    seniority: dict[str, Any],
) -> tuple[float, str | None, int | None]:
    """Return (grade_fit 0-100, flag, required_years).

    Flags roles pitched well below the user's level (overqualified risk) or
    well above it (out-of-reach risk). Neutral when no figure is parseable.
    """
    required = parse_required_years(jd_text)
    if required is None:
        return 80.0, None, None

    min_years = seniority.get("min_years_in_jd", 0)
    max_years = seniority.get("max_years_in_jd", 99)

    if required < min_years:
        return 60.0, f"role asks {required}y — below your level (overqualified risk)", required
    if required > max_years:
        return 50.0, f"role asks {required}y — above your level (stretch)", required
    return 100.0, None, required


# ---------------------------------------------------------------------------
# Exclusion gates (hard veto)
# ---------------------------------------------------------------------------

def title_excluded(title: str, target: dict[str, Any]) -> str | None:
    """Return the matched substring if the title is excluded, else None."""
    low = (title or "").lower()
    for sub in target.get("exclude_title_substrings", []):
        if sub.lower() in low:
            return sub
    return None


def company_excluded(company: str, target: dict[str, Any]) -> str | None:
    low = (company or "").lower()
    for name in target.get("exclude_companies", []):
        if name.lower() in low:
            return name
    return None


def domain_excluded(jd_text: str | None, target: dict[str, Any]) -> str | None:
    low = (jd_text or "").lower()
    for domain in target.get("exclude_domains", []):
        if domain.lower() in low:
            return domain
    return None


# ---------------------------------------------------------------------------
# Aggregate result
# ---------------------------------------------------------------------------

@dataclass
class RuleResult:
    market_tag: str
    auth_status: str
    auth_fit: float
    grade_fit: float
    required_years: int | None
    veto: bool
    veto_reason: str | None
    flags: list[str] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return {
            "market_tag": self.market_tag,
            "auth_status": self.auth_status,
            "veto": self.veto,
            "veto_reason": self.veto_reason,
            "required_years": self.required_years,
            "flags": self.flags,
        }


def apply_rules(job: dict[str, Any], profile: dict[str, Any]) -> RuleResult:
    """Run every deterministic gate for a single job.

    `job` needs keys: title, company, country, jd_text.
    """
    target = profile.get("target", {})
    scoring = profile.get("scoring", {})
    work_auth = profile.get("work_authorisation", {})
    seniority = profile.get("seniority", {})

    tag = market_tag(job.get("country"))
    status, auth_fit, auth_veto, auth_flag = auth_gate(
        job.get("country"), work_auth, scoring.get("hard_veto_on_auth", [])
    )
    grade_fit, grade_flag, required = grade_gate(job.get("jd_text"), seniority)

    flags: list[str] = []
    if auth_flag:
        flags.append(auth_flag)
    if grade_flag:
        flags.append(grade_flag)

    # Exclusions veto outright.
    veto = auth_veto
    veto_reason: str | None = None
    if auth_veto:
        veto_reason = f"auth: {status}"

    if (matched := title_excluded(job.get("title", ""), target)):
        veto, veto_reason = True, f"excluded title: {matched!r}"
    elif (matched := company_excluded(job.get("company", ""), target)):
        veto, veto_reason = True, f"excluded company: {matched!r}"
    elif (matched := domain_excluded(job.get("jd_text"), target)):
        veto, veto_reason = True, f"excluded domain: {matched!r}"

    return RuleResult(
        market_tag=tag,
        auth_status=status,
        auth_fit=auth_fit,
        grade_fit=grade_fit,
        required_years=required,
        veto=veto,
        veto_reason=veto_reason,
        flags=flags,
    )
