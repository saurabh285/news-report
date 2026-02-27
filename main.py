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


def _get_provider(config: dict) -> str:
    from ai.llm_client import resolve_provider  # noqa: PLC0415
    configured = (config.get("ai") or {}).get("provider", "")
    return resolve_provider(configured)


def _get_model(config: dict, provider: str) -> str:
    from ai.llm_client import resolve_model  # noqa: PLC0415
    configured = (config.get("ai") or {}).get("model", "")
    return resolve_model(provider, configured)


def _get_recipient(config: dict) -> str:
    return (
        config.get("email_recipient") or os.environ.get("EMAIL_RECIPIENT", "")
    ).strip()


# ---------------------------------------------------------------------------
# Agent pipeline
# ---------------------------------------------------------------------------
def _run_agent(config: dict) -> None:
    """Run the agent/LLM pipeline and send an HTML digest email.

    Can raise any exceptions thrown by :func:`ai.agent_runner.run_agent`; the
    caller may catch them for fallback behavior.
    """
    feeds = config.get("feeds") or []
    if not feeds:
        raise ValueError("No feeds defined in config.yaml.")

    recipient = _get_recipient(config)
    if not recipient:
        raise ValueError(
            "No email recipient — set 'email_recipient' in config.yaml "
            "or EMAIL_RECIPIENT env var."
        )

    provider = _get_provider(config)
    model    = _get_model(config, provider)

    from ai.agent_runner import run_agent        # noqa: PLC0415
    from ai.tools import send_email_html         # noqa: PLC0415
    from ai.email_template import render_html    # noqa: PLC0415

    max_per_feed = int((config.get("ai") or {}).get("max_per_feed", 5))
    output = run_agent(
        feeds=feeds,
        recipient=recipient,
        provider=provider,
        model=model,
        max_per_feed=max_per_feed,
    )
    html   = render_html(output)

    result = send_email_html(
        subject=output["subject"],
        html=html,
        to=recipient,
    )
    if not result["ok"]:
        raise RuntimeError(f"Email delivery failed: {result['error']}")


# ---------------------------------------------------------------------------
# Free-mode pipeline
# ---------------------------------------------------------------------------

def _run_free(config: dict) -> None:
    """Run a simple extractive pipeline using only Python.

    The implementation mirrors the agent pipeline for fetching and
    ranking articles, but generates a basic text summary for each article
    and sends it as an HTML-preformatted email via :func:`ai.tools.send_email_html`.
    """
    from ai.tools import fetch_rss, dedupe, rank, fetch_article_text, summarize, send_email_html  # noqa: PLC0415

    feeds = config.get("feeds") or []
    if not feeds:
        raise ValueError("No feeds defined in config.yaml.")

    recipient = _get_recipient(config)
    if not recipient:
        raise ValueError(
            "No email recipient — set 'email_recipient' in config.yaml "
            "or EMAIL_RECIPIENT env var."
        )

    max_per_feed = int((config.get("ai") or {}).get("max_per_feed", 5))

    # Step 1: fetch feeds
    all_articles = []
    for feed in feeds:
        try:
            arts = fetch_rss(feed)
            kept = arts[:max_per_feed]
            all_articles.extend(kept)
            logger.info(f"  {feed}: {len(arts)} entries → kept {len(kept)}")
        except Exception as exc:
            logger.warning(f"  Skipping feed {feed}: {exc}")

    if not all_articles:
        raise RuntimeError("No articles collected from any feed.")

    # Step 2: dedupe and rank
    deduped = dedupe(all_articles)
    ranked = rank(deduped, top_k=10)

    # Step 3: fetch text and summarise
    for art in ranked:
        result = fetch_article_text(art["url"])
        text = result.get("text") or ""
        art["text"] = text
        art["summary"] = summarize(text)

    # Build email body
    today = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).strftime("%Y-%m-%d")
    subject = f"Daily News Digest — {today}"
    body_lines = []
    for art in ranked:
        body_lines.append(f"{art['title']}\n{art['url']}\n{art.get('summary','(no summary)')}\n")
    body = "\n".join(body_lines)
    html = f"<pre>{body}</pre>"

    result = send_email_html(subject=subject, html=html, to=recipient)
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

    mode = (config.get("ai") or {}).get("mode", "agent").strip().lower()

    if mode == "free":
        try:
            _run_free(config)
        except Exception as exc:
            logger.error(f"Free-mode pipeline failed ({type(exc).__name__}: {exc})")
            sys.exit(1)
    else:
        try:
            _run_agent(config)
        except Exception as exc:
            logger.error(f"Agent failed ({type(exc).__name__}: {exc}) — falling back to free mode")
            try:
                _run_free(config)
            except Exception as exc2:
                logger.error(f"Fallback free pipeline also failed ({type(exc2).__name__}: {exc2})")
                sys.exit(1)


if __name__ == "__main__":
    main()
