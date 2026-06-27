"""LangGraph reply graph — Phase 4.

Flow: poll_replies -> parse_commands -> execute

  poll_replies   read unread replies in recent digest threads (Gmail)
  parse_commands parse each reply body into structured commands
  execute        idempotently claim each reply, run its commands, email an
                 acknowledgement, and mark the message read

Idempotency: claim_command() inserts on the unique gmail_message_id; a reply
already in the commands table returns no id and is skipped — so re-runs of the
8-hourly poller never double-process a reply.
"""

from __future__ import annotations

import logging
from typing import Any, TypedDict

from langgraph.graph import END, StateGraph

from agent.config import Settings
from agent.email import gmail
from agent.replies import persistence
from agent.replies.executor import compose_ack, execute_commands
from agent.replies.parser import parse_reply

logger = logging.getLogger(__name__)


class ReplyState(TypedDict, total=False):
    profile: dict[str, Any]
    settings: Settings
    service: Any
    unread_replies: list[dict]      # {message_id, thread_id, from, subject, body, digest_id, index_map}
    parsed_commands: list[dict]     # per-reply parsed commands attached
    execution_results: list[dict]
    errors: list[str]


def node_poll_replies(state: ReplyState) -> ReplyState:
    """Scan recent digest threads for unread replies."""
    settings = state.get("settings") or Settings.from_env()
    service = state.get("service") or gmail.build_service(settings)

    replies: list[dict] = []
    for digest in persistence.recent_digests():
        thread_id = digest.get("gmail_thread_id")
        if not thread_id:
            continue
        for msg in gmail.list_unread_replies(service, thread_id):
            msg["digest_id"] = digest["id"]
            msg["index_map"] = digest.get("index_map") or {}
            replies.append(msg)

    logger.info("Poller: found %d unread reply message(s)", len(replies))
    return {**state, "settings": settings, "service": service, "unread_replies": replies}


def node_parse_commands(state: ReplyState) -> ReplyState:
    """Parse each reply body into commands."""
    parsed: list[dict] = []
    for reply in state.get("unread_replies", []):
        commands = parse_reply(reply.get("body", ""))
        parsed.append({**reply, "commands": commands})
    return {**state, "parsed_commands": parsed}


def node_execute(state: ReplyState) -> ReplyState:
    """Idempotently claim each reply, run its commands, ack, and mark read."""
    settings = state["settings"]
    service = state["service"]
    results: list[dict] = []

    for reply in state.get("parsed_commands", []):
        message_id = reply["message_id"]
        commands = reply.get("commands", [])

        # Idempotency gate: claim the message before doing any work.
        command_id = persistence.claim_command(
            reply["digest_id"],
            message_id,
            reply.get("body", ""),
            [c.to_json() for c in commands],
        )
        if command_id is None:
            logger.info("Skipping already-processed reply %s", message_id)
            continue

        ack_lines = execute_commands(commands, reply.get("index_map", {}))
        body = compose_ack(reply.get("from", ""), ack_lines)

        subject = reply.get("subject") or "Job digest"
        if not subject.lower().startswith("re:"):
            subject = "Re: " + subject

        gmail.send_reply(
            service, settings.email_to, subject, body,
            thread_id=reply["thread_id"], in_reply_to=message_id,
        )
        gmail.mark_read(service, message_id)
        persistence.mark_command_processed(command_id)

        results.append({"message_id": message_id, "ack": ack_lines})

    logger.info("Executor: processed %d new reply message(s)", len(results))
    return {**state, "execution_results": results}


def build_reply_graph() -> StateGraph:
    graph = StateGraph(ReplyState)
    graph.add_node("poll_replies",   node_poll_replies)
    graph.add_node("parse_commands", node_parse_commands)
    graph.add_node("execute",        node_execute)
    graph.set_entry_point("poll_replies")
    graph.add_edge("poll_replies",   "parse_commands")
    graph.add_edge("parse_commands", "execute")
    graph.add_edge("execute",        END)
    return graph.compile()


def run_replies() -> ReplyState:
    """Entry point called from scripts/run_replies.py."""
    from agent.config import load_profile

    profile = load_profile()
    graph = build_reply_graph()
    return graph.invoke({"profile": profile, "errors": []})
