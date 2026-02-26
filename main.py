"""
main.py — Daily News Report Agent entry point.

Routing
-------
config.yaml → ai.mode controls which pipeline runs:

  free   (default) — deterministic extractive pipeline, no API key needed.
  claude / agent   — tool-calling Claude agent; falls back to free on any error.
"""

import calendar
import logging
import os
import re
import smtplib
from collections import Counter
from datetime import datetime, timezone
from email.mime.text import MIMEText

import feedparser
import requests
import trafilatura
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


def _get_mode(config: dict) -> str:
    """Return normalised ai.mode value: 'free', 'claude', or 'agent'."""
    mode = (config.get("ai") or {}).get("mode", "free")
    return str(mode).lower().strip()


def _get_model(config: dict) -> str:
    """
    Resolve the Claude model to use.

    Priority: ANTHROPIC_MODEL env var → config.yaml ai.model → cheap default.
    Delegates to ai.claude_client.resolve_model (lazy import keeps free mode
    independent of the anthropic package).
    """
    from ai.claude_client import resolve_model  # noqa: PLC0415
    configured = (config.get("ai") or {}).get("model", "")
    return resolve_model(configured)


def _get_recipient(config: dict) -> str:
    return (
        config.get("email_recipient") or os.environ.get("EMAIL_RECIPIENT", "")
    ).strip()


# ---------------------------------------------------------------------------
# Free-mode helpers  (self-contained deterministic pipeline)
# ---------------------------------------------------------------------------
_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; DailyNewsBot/1.0)"}


def _fetch_feed_entries(feed_url: str) -> list:
    logger.info(f"Fetching feed: {feed_url}")
    feed = feedparser.parse(feed_url)
    if feed.bozo:
        logger.warning(f"  Parse warning ({feed_url}): {feed.bozo_exception}")
    logger.info(f"  {len(feed.entries)} entries")
    return feed.entries


def _fetch_article_text(url: str):
    try:
        resp = requests.get(url, timeout=15, headers=_HEADERS)
        resp.raise_for_status()
        text = trafilatura.extract(resp.text)
        if not text:
            logger.debug(f"  No text extracted from {url}")
        return text
    except Exception as exc:
        logger.error(f"  Could not fetch {url}: {exc}")
        return None


def _recency_score(published: datetime) -> float:
    days = (datetime.now(timezone.utc) - published).total_seconds() / 86_400
    return 1.0 / (1.0 + days)


def _extractive_summary(text: str, num_sentences: int = 3) -> str:
    if not text:
        return ""
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    sentences = [s.strip() for s in sentences if len(s.strip()) > 30]
    if not sentences:
        return text[:300].strip()
    if len(sentences) <= num_sentences:
        return " ".join(sentences)

    words = re.findall(r"\b[a-z]{4,}\b", text.lower())
    freq = Counter(words)
    max_freq = max(freq.values(), default=1)
    norm_freq = {w: v / max_freq for w, v in freq.items()}

    def score(sent: str) -> float:
        ws = re.findall(r"\b[a-z]{4,}\b", sent.lower())
        return sum(norm_freq.get(w, 0) for w in ws) / max(len(ws), 1)

    ranked = sorted(range(len(sentences)), key=lambda i: score(sentences[i]), reverse=True)
    top_indices = sorted(ranked[:num_sentences])
    return " ".join(sentences[i] for i in top_indices)


def _parse_published(entry) -> datetime:
    for field in ("published_parsed", "updated_parsed"):
        tp = entry.get(field)
        if tp:
            try:
                return datetime.fromtimestamp(calendar.timegm(tp), tz=timezone.utc)
            except Exception:
                pass
    return datetime.now(timezone.utc)


def _build_plain_body(articles: list) -> str:
    date_str = datetime.now().strftime("%B %d, %Y")
    lines = [f"Daily News Report — {date_str}", "=" * 52, ""]
    for idx, art in enumerate(articles, 1):
        title = art.get("title") or art["url"]
        pub = art["published"].strftime("%Y-%m-%d %H:%M UTC")
        summary = art.get("summary", "")
        lines += [
            f"{idx}. {title}",
            f"   Published : {pub}",
            f"   URL       : {art['url']}",
        ]
        if summary:
            for line in summary.splitlines():
                lines.append(f"   {line}")
        lines.append("")
    lines.append("—\nGenerated by Daily News Report Agent")
    return "\n".join(lines)


def _send_plain_email(subject: str, body: str, recipient: str) -> None:
    user = os.environ.get("GMAIL_USER")
    password = os.environ.get("GMAIL_APP_PASSWORD")
    if not user or not password:
        raise RuntimeError(
            "GMAIL_USER and GMAIL_APP_PASSWORD environment variables must be set."
        )
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = user
    msg["To"] = recipient

    logger.info("Connecting to Gmail SMTP …")
    with smtplib.SMTP("smtp.gmail.com", 587) as server:
        server.ehlo()
        server.starttls()
        server.login(user, password)
        server.send_message(msg)
    logger.info(f"Email sent to {recipient}")


# ---------------------------------------------------------------------------
# Free-mode pipeline
# ---------------------------------------------------------------------------
def _run_free_mode(config: dict) -> None:
    """Deterministic pipeline: RSS → dedup → rank → summarise → email."""
    logger.info("Running in FREE (deterministic) mode")

    feeds = config.get("feeds") or []
    if not feeds:
        logger.error("No feeds defined in config.yaml — nothing to do.")
        return

    seen_urls: set = set()
    articles: list = []

    for feed_url in feeds:
        try:
            entries = _fetch_feed_entries(feed_url)
        except Exception as exc:
            logger.error(f"Skipping feed {feed_url}: {exc}")
            continue

        for entry in entries:
            url = (entry.get("link") or "").strip()
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)
            articles.append(
                {
                    "url": url,
                    "title": (entry.get("title") or url).strip(),
                    "published": _parse_published(entry),
                }
            )

    logger.info(f"Total unique articles collected: {len(articles)}")

    if not articles:
        logger.error("No articles found — aborting.")
        return

    for art in articles:
        art["text"] = _fetch_article_text(art["url"])

    articles = [a for a in articles if a.get("text")]
    logger.info(f"Articles with extractable text: {len(articles)}")

    if not articles:
        logger.error("No articles with readable text — aborting.")
        return

    for art in articles:
        art["score"] = _recency_score(art["published"])
    articles.sort(key=lambda x: x["score"], reverse=True)
    top = articles[:10]
    logger.info(f"Selected top {len(top)} articles")

    for art in top:
        art["summary"] = _extractive_summary(art["text"])

    recipient = _get_recipient(config)
    if not recipient:
        logger.error(
            "No email recipient — set 'email_recipient' in config.yaml "
            "or EMAIL_RECIPIENT env var."
        )
        return

    subject = f"Daily News Report — {datetime.now().strftime('%Y-%m-%d')}"
    body = _build_plain_body(top)

    try:
        _send_plain_email(subject, body, recipient)
    except Exception as exc:
        logger.error(f"Failed to send email: {exc}")


# ---------------------------------------------------------------------------
# Agent-mode pipeline
# ---------------------------------------------------------------------------
def _run_agent_mode(config: dict) -> None:
    """Tool-calling Claude agent pipeline → HTML email."""
    logger.info("Running in AGENT mode")

    feeds = config.get("feeds") or []
    if not feeds:
        logger.error("No feeds defined in config.yaml — nothing to do.")
        return

    recipient = _get_recipient(config)
    if not recipient:
        logger.error(
            "No email recipient — set 'email_recipient' in config.yaml "
            "or EMAIL_RECIPIENT env var."
        )
        return

    model = _get_model(config)

    # Lazy import so free mode never requires anthropic to be installed
    from ai.agent_runner import run_agent  # noqa: PLC0415
    from ai.tools import send_email_html  # noqa: PLC0415

    output = run_agent(feeds=feeds, recipient=recipient, model=model)

    result = send_email_html(
        subject=output["subject"],
        html=output["html_body"],
        to=recipient,
    )
    if not result["ok"]:
        raise RuntimeError(f"send_email_html failed: {result['error']}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main() -> None:
    try:
        config = load_config()
    except Exception as exc:
        logger.error(f"Failed to load config: {exc}")
        return

    mode = _get_mode(config)
    logger.info(f"ai.mode = {mode!r} (agent always attempted first)")

    # Always try agent mode first, regardless of ai.mode setting.
    # Free mode is the universal fallback when agent fails for any reason.
    try:
        _run_agent_mode(config)
        return
    except Exception as exc:
        logger.error(
            f"Agent mode failed ({type(exc).__name__}: {exc}). "
            "Falling back to free (deterministic) mode."
        )

    _run_free_mode(config)


if __name__ == "__main__":
    main()
