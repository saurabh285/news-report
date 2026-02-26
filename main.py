"""
main.py — Daily News Report Agent entry point.

Runs the Claude tool-calling agent to produce a daily HTML digest email.
Requires ANTHROPIC_API_KEY, GMAIL_USER, and GMAIL_APP_PASSWORD to be set.
"""

import logging
import os
import sys

import yaml



# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
def load_config(path: str = "config.yaml") -> dict:
    if not os.path.exists(path):
        raise FileNotFoundError(f"Config file not found: {path}")
    with open(path, "r") as fh:
        cfg = yaml.safe_load(fh)
    if not isinstance(cfg, dict):
        raise ValueError("config.yaml must contain a YAML mapping")
    return cfg


def _get_model(config: dict) -> str:
    """
    Resolve the Claude model to use.

    Priority: ANTHROPIC_MODEL env var → config.yaml ai.model → cheap default.
    """
    from ai.claude_client import resolve_model  # noqa: PLC0415
    configured = (config.get("ai") or {}).get("model", "")
    return resolve_model(configured)


def _get_recipient(config: dict) -> str:
    return (
        config.get("email_recipient") or os.environ.get("EMAIL_RECIPIENT", "")
    ).strip()


# ---------------------------------------------------------------------------
# Agent pipeline
# ---------------------------------------------------------------------------
def _run_agent(config: dict) -> None:
    """Run the Claude agent and send the resulting HTML digest email."""
    feeds = config.get("feeds") or []
    if not feeds:
        raise ValueError("No feeds defined in config.yaml.")

    recipient = _get_recipient(config)
    if not recipient:
        raise ValueError(
            "No email recipient — set 'email_recipient' in config.yaml "
            "or EMAIL_RECIPIENT env var."
        )

    model = _get_model(config)

    from ai.agent_runner import run_agent        # noqa: PLC0415
    from ai.tools import send_email_html         # noqa: PLC0415
    from ai.email_template import render_html    # noqa: PLC0415

    max_per_feed = int((config.get("ai") or {}).get("max_per_feed", 5))
    output = run_agent(feeds=feeds, recipient=recipient, model=model, max_per_feed=max_per_feed)
    html   = render_html(output)

    result = send_email_html(
        subject=output["subject"],
        html=html,
        to=recipient,
    )
    if not result["ok"]:
        raise RuntimeError(f"Email delivery failed: {result['error']}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main() -> None:
    try:
        config = load_config()
    except Exception as exc:
        logger.error(f"Failed to load config: {exc}")
        sys.exit(1)

    try:
        _run_agent(config)
    except Exception as exc:
        logger.error(f"Agent failed ({type(exc).__name__}: {exc})")
        sys.exit(1)


if __name__ == "__main__":
    main()
