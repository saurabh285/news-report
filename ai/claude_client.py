"""
ai/claude_client.py — Thin wrapper around the Anthropic Messages API.

Raises a clean RuntimeError (not an ImportError or AttributeError) when
either the anthropic package is missing or ANTHROPIC_API_KEY is not set,
so callers can catch a single exception type for the "not configured" case.

Model resolution priority (highest → lowest):
  1. ANTHROPIC_MODEL environment variable
  2. ai.model in config.yaml  (passed as ``configured`` to resolve_model)
  3. _CHEAP_DEFAULT — the cheapest available Haiku model
"""

import logging
import os

logger = logging.getLogger(__name__)

# Cheapest models in preferred order.  resolve_model() falls back through
# this list when the configured model is empty or looks invalid.
# Real Anthropic model IDs, cheapest first.
# claude-haiku-4-5-20251001  — Claude Haiku 4.5  (cheapest currently active model)
# claude-sonnet-4-6           — Claude Sonnet 4.6 (fallback if Haiku unavailable)
#
# NOTE: claude-3-5-haiku-20241022 and claude-3-haiku-20240307 were both
# retired by Anthropic on 2026-02-19 and will return 404.
_CHEAP_FALLBACKS: tuple = ("claude-haiku-4-5-20251001", "claude-sonnet-4-6")
_CHEAP_DEFAULT: str = _CHEAP_FALLBACKS[0]

_DEFAULT_MAX_TOKENS = 4096


def resolve_model(configured: str = "") -> str:
    """
    Resolve the Claude model to use, in priority order:

    1. ``ANTHROPIC_MODEL`` environment variable (highest priority).
    2. ``configured`` — the value from ``config.yaml ai.model``.
    3. ``_CHEAP_DEFAULT`` — cheapest Haiku model (lowest priority).

    A warning is logged and the cheap default is used when the resolved
    value is empty or does not look like a valid Claude model ID (i.e. it
    does not contain the word ``claude``).

    Parameters
    ----------
    configured : str
        The ``ai.model`` value read from ``config.yaml`` (may be empty).

    Returns
    -------
    str
        A non-empty model ID string, guaranteed to contain ``"claude"``.
    """
    # 1. Env-var override
    env_model = os.environ.get("ANTHROPIC_MODEL", "").strip()
    if env_model:
        logger.info(f"Model overridden by ANTHROPIC_MODEL env var: {env_model!r}")
        return env_model

    # 2. Config-provided value
    if configured and configured.strip():
        model = configured.strip()
        if "claude" in model.lower():
            return model
        # Doesn't look like a Claude model ID
        logger.warning(
            f"Configured model {model!r} does not look like a valid Claude model ID "
            f"(expected a string containing 'claude'). "
            f"Falling back to cheap default {_CHEAP_DEFAULT!r}."
        )

    # 3. Cheap default
    logger.info(f"No valid model configured — using cheap default {_CHEAP_DEFAULT!r}")
    return _CHEAP_DEFAULT


def _get_client():
    """Return an initialised Anthropic client, or raise RuntimeError."""
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY environment variable is not set. "
            "Agent mode requires a valid Anthropic API key. "
            "See the README for setup instructions."
        )

    try:
        import anthropic  # noqa: PLC0415 — lazy import intentional
    except ImportError:
        raise RuntimeError(
            "The 'anthropic' package is not installed. "
            "Run: pip install 'anthropic>=0.25'"
        )

    return anthropic.Anthropic(api_key=api_key)


def call_claude(
    messages: list,
    tools: list = None,
    system: str = None,
    model: str = _CHEAP_DEFAULT,
    max_tokens: int = _DEFAULT_MAX_TOKENS,
):
    """
    Call the Claude Messages API and return the full response object.

    Parameters
    ----------
    messages : list
        Conversation history in the Anthropic messages format.
    tools : list, optional
        Tool schemas (list of dicts with ``name``, ``description``,
        ``input_schema``).
    system : str, optional
        System prompt text.
    model : str
        Claude model ID.
    max_tokens : int
        Maximum tokens in the response.

    Returns
    -------
    anthropic.types.Message
        The raw response object.  Callers inspect ``.stop_reason`` and
        ``.content`` to decide the next step.

    Raises
    ------
    RuntimeError
        When the API key or package is missing (clean error for callers).
    anthropic.APIError subclasses
        Propagated as-is so callers can inspect them if needed.
    """
    client = _get_client()

    kwargs: dict = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": messages,
    }
    if system:
        kwargs["system"] = system
    if tools:
        kwargs["tools"] = tools

    logger.debug(
        f"call_claude model={model} messages={len(messages)} "
        f"tools={len(tools) if tools else 0}"
    )

    response = client.messages.create(**kwargs)
    logger.debug(f"  stop_reason={response.stop_reason} usage={response.usage}")
    return response
