"""LangGraph discover graph — Phase 2.

Flow: discover → dedupe → score → filter → notify (stub)

Scoring and filtering are implemented in Phase 2; the notify node (digest email)
is filled in Phase 3.
"""

from __future__ import annotations

import logging
import os
from typing import Any, TypedDict

from langgraph.graph import END, StateGraph

from agent.config import Settings, load_profile
from agent.discovery.dedupe import dedupe, persist_jobs
from agent.discovery.greenhouse import GreenhouseSource
from agent.discovery.lever import LeverSource
from agent.email.digest import render_digest
from agent.email.gmail import send_digest
from agent.email.persistence import current_run_id, record_digest
from agent.scoring.filter import filter_jobs
from agent.scoring.persistence import persist_scores
from agent.scoring.score import score_jobs

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Graph state
# ---------------------------------------------------------------------------

class DiscoverState(TypedDict, total=False):
    profile: dict[str, Any]
    raw_postings: list[Any]          # list[JobPosting] (before dedupe)
    new_postings: list[Any]          # list[JobPosting] (after dedupe)
    persisted_jobs: list[dict]       # list of DB row dicts (with UUIDs)
    scored_jobs: list[Any]           # list[ScoredJob] — Phase 2
    filtered_jobs: list[Any]         # list[ScoredJob] shortlist — Phase 2
    digest_sent: bool                # filled in Phase 3
    errors: list[str]


# ---------------------------------------------------------------------------
# Node functions
# ---------------------------------------------------------------------------

def node_discover(state: DiscoverState) -> DiscoverState:
    """Pull postings from all configured sources."""
    profile = state["profile"]
    discovery_cfg = profile.get("discovery", {})
    sources_cfg = discovery_cfg.get("sources", {})
    keywords: list[str] = discovery_cfg.get("search_keywords", [])

    all_postings: list[Any] = []

    if sources_cfg.get("greenhouse", {}).get("enabled"):
        source = GreenhouseSource()
        gh_cfg = sources_cfg["greenhouse"]
        postings = source.fetch(gh_cfg, keywords)
        all_postings.extend(postings)
        logger.info("Discovery: Greenhouse returned %d postings", len(postings))

    if sources_cfg.get("lever", {}).get("enabled"):
        source = LeverSource()
        lv_cfg = sources_cfg["lever"]
        postings = source.fetch(lv_cfg, keywords)
        all_postings.extend(postings)
        logger.info("Discovery: Lever returned %d postings", len(postings))

    logger.info("Discovery: %d total postings from all sources", len(all_postings))
    return {**state, "raw_postings": all_postings}


def node_dedupe(state: DiscoverState) -> DiscoverState:
    """Remove postings already in the database."""
    raw = state.get("raw_postings", [])
    new = dedupe(raw)
    persisted = persist_jobs(new)
    return {**state, "new_postings": new, "persisted_jobs": persisted}


def node_score(state: DiscoverState) -> DiscoverState:
    """Rule-gate + LLM-fit score each new job, then persist the scores."""
    profile = state["profile"]
    jobs = state.get("persisted_jobs", [])
    scored = score_jobs(jobs, profile)
    persist_scores(scored)
    return {**state, "scored_jobs": scored}


def node_filter(state: DiscoverState) -> DiscoverState:
    """Filter to the top-N shortlist by composite score."""
    profile = state["profile"]
    top_n_raw = os.environ.get("DIGEST_TOP_N", "")
    top_n = int(top_n_raw) if top_n_raw.strip().isdigit() else None
    shortlist = filter_jobs(state.get("scored_jobs", []), profile, top_n_override=top_n)
    return {**state, "filtered_jobs": shortlist}


def node_notify(state: DiscoverState) -> DiscoverState:
    """Render and send the weekly digest email, then record it for reply resolution."""
    shortlist = state.get("filtered_jobs", [])
    if not shortlist:
        logger.info("Notify: no roles passed the filter — no digest sent")
        return {**state, "digest_sent": False}

    subject, text_body, html_body, index_map = render_digest(shortlist)

    if os.environ.get("DIGEST_DRY_RUN", "").strip().lower() in ("1", "true", "yes"):
        logger.info("Notify: DIGEST_DRY_RUN set — not sending. Subject: %s", subject)
        logger.info("Notify: digest body:\n%s", text_body)
        return {**state, "digest_sent": False}

    settings = Settings.from_env()
    message_id, thread_id = send_digest(settings, subject, text_body, html_body)
    record_digest(current_run_id(), thread_id, message_id, index_map)
    return {**state, "digest_sent": True}


# ---------------------------------------------------------------------------
# Graph assembly
# ---------------------------------------------------------------------------

def build_discover_graph() -> StateGraph:
    graph = StateGraph(DiscoverState)

    graph.add_node("discover", node_discover)
    graph.add_node("dedupe",   node_dedupe)
    graph.add_node("score",    node_score)
    graph.add_node("filter",   node_filter)
    graph.add_node("notify",   node_notify)

    graph.set_entry_point("discover")
    graph.add_edge("discover", "dedupe")
    graph.add_edge("dedupe",   "score")
    graph.add_edge("score",    "filter")
    graph.add_edge("filter",   "notify")
    graph.add_edge("notify",   END)

    return graph.compile()


def run_discover(profile_path: str | None = None) -> DiscoverState:
    """Entry point called from scripts/run_discover.py and tests."""
    profile = load_profile(profile_path)
    graph = build_discover_graph()
    initial_state: DiscoverState = {
        "profile": profile,
        "raw_postings": [],
        "new_postings": [],
        "persisted_jobs": [],
        "scored_jobs": [],
        "filtered_jobs": [],
        "digest_sent": False,
        "errors": [],
    }
    return graph.invoke(initial_state)
