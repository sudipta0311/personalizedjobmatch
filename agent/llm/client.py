"""LLM provider abstraction — Phase 2+ (multi-provider).

The agent is provider-agnostic. **OpenAI is the default**; Anthropic is selected
per node via `profile.yaml` (`models.<node>.provider: anthropic`). This module
exposes one function — `parse_structured` — that returns a validated Pydantic
model regardless of provider, so callers (scoring, and later tailoring) never
branch on the provider themselves.

  * OpenAI:    chat.completions with response_format=json_object → JSON → Pydantic
  * Anthropic: messages.parse with output_format=<PydanticModel>

The SDK clients read their API keys from the environment (OPENAI_API_KEY /
ANTHROPIC_API_KEY). Clients are injectable so tests never hit the network.
"""

from __future__ import annotations

import logging
from typing import Any, TypeVar

from pydantic import BaseModel

logger = logging.getLogger(__name__)

DEFAULT_PROVIDER = "openai"
_DEFAULT_MODELS = {
    "openai": "gpt-4o-mini",
    "anthropic": "claude-sonnet-4-6",
}

T = TypeVar("T", bound=BaseModel)


def default_model(provider: str) -> str:
    return _DEFAULT_MODELS.get(provider, _DEFAULT_MODELS["openai"])


def resolve_node(
    profile: dict[str, Any], node: str
) -> tuple[str, str]:
    """Resolve (provider, model) for a node from profile.yaml `models.<node>`.

    Accepts either the structured form `{provider, model}` or a bare model
    string (back-compat); falls back to the default provider/model.
    """
    cfg = (profile.get("models", {}) or {}).get(node)
    if isinstance(cfg, dict):
        provider = cfg.get("provider", DEFAULT_PROVIDER)
        model = cfg.get("model") or default_model(provider)
        return provider, model
    if isinstance(cfg, str) and cfg:
        # bare string: infer provider from the model id
        provider = "anthropic" if cfg.startswith("claude") else "openai"
        return provider, cfg
    return DEFAULT_PROVIDER, default_model(DEFAULT_PROVIDER)


def build_client(provider: str) -> Any:
    """Construct an SDK client for the provider (key read from env)."""
    if provider == "openai":
        import openai
        return openai.OpenAI()
    if provider == "anthropic":
        import anthropic
        return anthropic.Anthropic()
    raise ValueError(f"Unknown LLM provider: {provider!r}")


def parse_structured(
    *,
    provider: str,
    model: str,
    system: str,
    user: str,
    schema_model: type[T],
    client: Any | None = None,
    max_tokens: int = 1024,
) -> T:
    """Return a validated `schema_model` from the chosen provider."""
    client = client or build_client(provider)
    if provider == "openai":
        return _openai_parse(client, model, system, user, schema_model, max_tokens)
    if provider == "anthropic":
        return _anthropic_parse(client, model, system, user, schema_model, max_tokens)
    raise ValueError(f"Unknown LLM provider: {provider!r}")


def _openai_parse(
    client: Any, model: str, system: str, user: str,
    schema_model: type[T], max_tokens: int,
) -> T:
    resp = client.chat.completions.create(
        model=model,
        max_tokens=max_tokens,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    )
    content = resp.choices[0].message.content
    return schema_model.model_validate_json(content)


def _anthropic_parse(
    client: Any, model: str, system: str, user: str,
    schema_model: type[T], max_tokens: int,
) -> T:
    resp = client.messages.parse(
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
        output_format=schema_model,
    )
    return resp.parsed_output
