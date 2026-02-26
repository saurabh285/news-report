"""
ai/tools.py — Python implementations of every tool available to the agent.

Each function is a plain Python callable.  The JSON schemas that describe
these functions to Claude live in agent_runner.py (TOOL_SCHEMAS).
"""

import calendar
import logging
import os
import re
import smtplib
import urllib.parse
from collections import Counter
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import feedparser
import requests
import trafilatura

logger = logging.getLogger(__name__)

_HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}

# Query parameters that carry no semantic value and should be stripped when
# canonicalising a URL for deduplication purposes.
_TRACKING_PARAMS: frozenset = frozenset(
    [
        "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
        "utm_id", "fbclid", "gclid", "mc_cid", "mc_eid", "ref", "source",
        "_ga", "igshid", "twclid",
    ]
)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _canonicalize_url(url: str) -> str:
    """Strip tracking params, fragment, and normalise the URL."""
    try:
        parsed = urllib.parse.urlparse(url.strip())
        params = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
        cleaned = {k: v for k, v in params.items() if k.lower() not in _TRACKING_PARAMS}
        new_query = urllib.parse.urlencode(cleaned, doseq=True)
        return urllib.parse.urlunparse(parsed._replace(query=new_query, fragment=""))
    except Exception:
        return url.strip()


def _recency_score(published_ts: float) -> float:
    days = (datetime.now(timezone.utc).timestamp() - published_ts) / 86_400
    return 1.0 / (1.0 + max(days, 0.0))


# ---------------------------------------------------------------------------
# Tool: fetch_rss
# ---------------------------------------------------------------------------

def fetch_rss(source_url: str) -> list:
    """
    Fetch and parse an RSS / Atom feed.

    Returns a list of dicts:
        {title: str, url: str, published_ts: int, source: str}
    """
    logger.info(f"[tool] fetch_rss: {source_url}")
    try:
        feed = feedparser.parse(source_url)
    except Exception as exc:
        logger.error(f"  feedparser raised: {exc}")
        return []

    if feed.bozo:
        logger.warning(f"  Feed parse warning: {feed.bozo_exception}")

    results = []
    for entry in feed.entries:
        url = (entry.get("link") or "").strip()
        if not url:
            continue

        pub_ts = None
        for field in ("published_parsed", "updated_parsed"):
            tp = entry.get(field)
            if tp:
                try:
                    pub_ts = int(calendar.timegm(tp))
                    break
                except Exception:
                    pass
        if pub_ts is None:
            pub_ts = int(datetime.now(timezone.utc).timestamp())

        results.append(
            {
                "title": (entry.get("title") or url).strip(),
                "url": url,
                "published_ts": pub_ts,
                "source": source_url,
            }
        )

    logger.info(f"  {len(results)} entries fetched from {source_url}")
    return results


# ---------------------------------------------------------------------------
# Tool: fetch_article_text
# ---------------------------------------------------------------------------

def fetch_article_text(url: str) -> dict:
    """
    Download a URL and extract readable article text using trafilatura.

    Returns:
        {url: str, text: str, extracted_ok: bool}
    """
    logger.info(f"[tool] fetch_article_text: {url}")
    try:
        resp = requests.get(url, timeout=15, headers=_HEADERS)
        resp.raise_for_status()
        text = trafilatura.extract(resp.text) or ""
        ok = bool(text)
        if not ok:
            logger.debug(f"  trafilatura returned empty for {url}")
        return {"url": url, "text": text, "extracted_ok": ok}
    except Exception as exc:
        logger.error(f"  fetch_article_text failed ({url}): {exc}")
        return {"url": url, "text": "", "extracted_ok": False}


# ---------------------------------------------------------------------------
# Tool: dedupe
# ---------------------------------------------------------------------------

def dedupe(items: list) -> list:
    """
    Remove duplicate articles by canonicalized URL (strips tracking params).

    Input / output: list of dicts that must each have a ``url`` field.
    """
    logger.info(f"[tool] dedupe: {len(items)} items in")
    seen: set = set()
    out: list = []
    for item in items:
        canon = _canonicalize_url(item.get("url", ""))
        if canon and canon not in seen:
            seen.add(canon)
            out.append({**item, "url": canon})
    logger.info(f"  {len(out)} items after dedup ({len(items) - len(out)} removed)")
    return out


# ---------------------------------------------------------------------------
# Tool: rank
# ---------------------------------------------------------------------------

def rank(items: list, top_k: int = 10) -> list:
    """
    Rank article dicts by recency-decay score and return the top ``top_k``.

    Expects each item to carry a ``published_ts`` (Unix timestamp int).
    """
    logger.info(f"[tool] rank: {len(items)} items → top {top_k}")
    ranked = sorted(items, key=lambda i: _recency_score(i.get("published_ts", 0)), reverse=True)
    top = ranked[:top_k]
    logger.info(f"  Returning {len(top)} items")
    return top


# ---------------------------------------------------------------------------
# Tool: send_email_html  (called by agent_runner, NOT exposed to Claude)
# ---------------------------------------------------------------------------

def send_email_html(subject: str, html: str, to: str) -> dict:
    """
    Send an HTML email via Gmail SMTP.

    Reads GMAIL_USER and GMAIL_APP_PASSWORD from the environment.
    Returns {ok: bool, error: str|None}.
    """
    logger.info(f"[tool] send_email_html: subject={subject!r}, to={to}")
    user = os.environ.get("GMAIL_USER")
    password = os.environ.get("GMAIL_APP_PASSWORD")
    if not user or not password:
        return {
            "ok": False,
            "error": "GMAIL_USER and GMAIL_APP_PASSWORD environment variables must be set.",
        }
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = user
        msg["To"] = to
        msg.attach(MIMEText(html, "html", "utf-8"))

        with smtplib.SMTP("smtp.gmail.com", 587) as server:
            server.ehlo()
            server.starttls()
            server.login(user, password)
            server.send_message(msg)

        logger.info(f"  HTML email sent to {to}")
        return {"ok": True, "error": None}
    except Exception as exc:
        logger.error(f"  send_email_html failed: {exc}")
        return {"ok": False, "error": str(exc)}
