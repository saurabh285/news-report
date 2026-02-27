"""
ai/llm_client.py — Provider-agnostic LLM interface.

Supported providers:
  gemini     — Google Gemini (free tier; uses GEMINI_API_KEY)
  anthropic  — Anthropic Claude  (uses ANTHROPIC_API_KEY)
  openai     — OpenAI             (uses OPENAI_API_KEY)

Provider selection priority (highest → lowest):
  1. LLM_PROVIDER env var
  2. config.yaml  ai.provider
  3. Auto-detect from whichever API key is present (gemini → anthropic → openai)
  4. Nothing found → RuntimeError with a helpful message

Model selection priority (per provider):
  1. {PROVIDER}_MODEL env var  (e.g. GEMINI_MODEL, ANTHROPIC_MODEL, OPENAI_MODEL)
  2. config.yaml  ai.model
  3. Provider default (cheapest / free-tier model)
"""

from __future__ import annotations
import logging
import os

logger = logging.getLogger(__name__)

# Default (cheapest / free-tier) model per provider.  These values are
# used when neither the per-provider MODEL env var nor config.yaml
# ai.model override is provided.
_DEFAULTS: dict[str, str] = {
    "gemini":    "gemini-1.5-flash",
    "anthropic": "claude-haiku-4-5-20251001",
    "openai":    "gpt-4o-mini",
}

# Environment variable that holds the API key for each provider.  The
# resolve_provider() auto-detection loop iterates through this mapping in
# insertion order, so providers earlier in the dict have higher priority
# when multiple keys are set.
_KEY_ENVS: dict[str, str] = {
    "gemini":    "GEMINI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "openai":    "OPENAI_API_KEY",
}


# ---------------------------------------------------------------------------
# Provider / model resolution
# ---------------------------------------------------------------------------

def resolve_provider(configured: str = "") -> str:
    """
    Resolve which LLM provider to use.

    Parameters
    ----------
    configured : str
        Value of ``ai.provider`` from config.yaml (may be empty).

    Returns
    -------
    str
        One of ``"gemini"``, ``"anthropic"``, or ``"openai"``.
    """
    # 1. Explicit env-var override
    env_provider = os.environ.get("LLM_PROVIDER", "").strip().lower()
    if env_provider:
        _check_provider(env_provider, "LLM_PROVIDER env var")
        logger.info(f"Provider from LLM_PROVIDER env var: {env_provider!r}")
        return env_provider

    # 2. Config-file value
    if configured and configured.strip():
        p = configured.strip().lower()
        _check_provider(p, "config.yaml ai.provider")
        logger.info(f"Provider from config.yaml: {p!r}")
        return p

    # 3. Auto-detect from whichever API key is present
    for provider, key_env in _KEY_ENVS.items():
        if os.environ.get(key_env, "").strip():
            logger.info(f"Auto-detected provider {provider!r} (found {key_env})")
            return provider

    raise RuntimeError(
        "No LLM provider configured and no API key found. "
        "Set one of the following environment variables: "
        + ", ".join(_KEY_ENVS.values())
        + ". Or set LLM_PROVIDER / ai.provider in config.yaml."
    )


def resolve_model(provider: str, configured: str = "") -> str:
    """
    Resolve the model ID for the given provider.

    Parameters
    ----------
    provider : str
        The provider returned by :func:`resolve_provider`.
    configured : str
        Value of ``ai.model`` from config.yaml (may be empty).

    Returns
    -------
    str
        A non-empty model ID string.
    """
    # 1. Per-provider env-var override  (e.g. GEMINI_MODEL, ANTHROPIC_MODEL)
    env_key   = f"{provider.upper()}_MODEL"
    env_model = os.environ.get(env_key, "").strip()
    if env_model:
        logger.info(f"Model overridden by {env_key}: {env_model!r}")
        return env_model

    # 2. Config-file value
    if configured and configured.strip():
        logger.info(f"Using configured model: {configured.strip()!r}")
        return configured.strip()

    # 3. Provider default
    if provider not in _DEFAULTS:
        raise RuntimeError(
            f"Unknown provider {provider!r} passed to resolve_model. "
            f"Expected one of: {', '.join(_DEFAULTS)}"
        )
    default = _DEFAULTS[provider]
    logger.info(f"Using default model for {provider!r}: {default!r}")
    return default


def _check_provider(provider: str, source: str) -> None:
    if provider not in _DEFAULTS:
        raise RuntimeError(
            f"{source} is set to {provider!r}, which is not supported. "
            f"Choose one of: {', '.join(_DEFAULTS)}"
        )


# ---------------------------------------------------------------------------
# Unified call interface
# ---------------------------------------------------------------------------

def call_llm(
    user_message: str,
    system: str,
    provider: str,
    model: str,
    max_tokens: int = 8192,
) -> str:
    """
    Call the specified LLM provider and return the plain-text response.

    Parameters
    ----------
    user_message : str
        The user-turn message content.
    system : str
        System prompt text.
    provider : str
        One of ``"gemini"``, ``"anthropic"``, ``"openai"``.
    model : str
        Model ID appropriate for the provider.
    max_tokens : int
        Maximum output tokens.

    Returns
    -------
    str
        The text content of the model's response.
    """
    logger.info(f"Calling LLM: provider={provider!r} model={model!r}")
    if provider == "gemini":
        return _call_gemini(user_message, system, model, max_tokens)
    if provider == "anthropic":
        return _call_anthropic(user_message, system, model, max_tokens)
    if provider == "openai":
        return _call_openai(user_message, system, model, max_tokens)
    raise RuntimeError(f"Unknown provider: {provider!r}")


# ---------------------------------------------------------------------------
# Provider implementations
# ---------------------------------------------------------------------------

def _call_gemini(user_message: str, system: str, model: str, max_tokens: int) -> str:
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError(
            "GEMINI_API_KEY is not set. "
            "Get a free key at https://aistudio.google.com/apikey"
        )
    try:
        from google import genai                # noqa: PLC0415
        from google.genai import types          # noqa: PLC0415
    except ImportError:
        raise RuntimeError(
            "The 'google-genai' package is not installed. "
            "Run: pip install google-genai"
        )

    client   = genai.Client(api_key=api_key)
    response = client.models.generate_content(
        model=model,
        contents=user_message,
        config=types.GenerateContentConfig(
            system_instruction=system,
            max_output_tokens=max_tokens,
            response_mime_type="application/json",
        ),
    )
    try:
        return response.text
    except ValueError:
        raise RuntimeError(
            f"Gemini API returned no text. Candidates: {getattr(response, 'candidates', None)}"
        )


def _call_anthropic(user_message: str, system: str, model: str, max_tokens: int) -> str:
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set.")
    try:
        import anthropic  # noqa: PLC0415
    except ImportError:
        raise RuntimeError(
            "The 'anthropic' package is not installed. "
            "Run: pip install anthropic"
        )

    client   = anthropic.Anthropic(api_key=api_key)
    response = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user_message}],
    )
    if response.stop_reason == "max_tokens":
        raise RuntimeError(
            "Anthropic hit the output token limit. "
            "Try reducing max_per_feed in config.yaml."
        )
    return "".join(b.text for b in response.content if hasattr(b, "text"))


def _call_openai(user_message: str, system: str, model: str, max_tokens: int) -> str:
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set.")
    try:
        from openai import OpenAI  # noqa: PLC0415
    except ImportError:
        raise RuntimeError(
            "The 'openai' package is not installed. "
            "Run: pip install openai"
        )

    client   = OpenAI(api_key=api_key)
    response = client.chat.completions.create(
        model=model,
        max_tokens=max_tokens,
        messages=[
            {"role": "system", "content": system},
            {"role": "user",   "content": user_message},
        ],
    )
    if response.choices[0].finish_reason == "length":
        raise RuntimeError(
            "OpenAI hit the output token limit. "
            "Try reducing max_per_feed in config.yaml."
        )
    return response.choices[0].message.content
