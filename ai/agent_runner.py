"""
ai/agent_runner.py

Pipeline
--------
All data-plumbing steps run in Python (no tool-calling loop):
  1. Fetch RSS feeds          → ai/tools.py fetch_rss()
  2. Dedupe + rank            → ai/tools.py dedupe() / rank()
  3. Fetch article text       → ai/tools.py fetch_article_text()
  4. Call Claude ONCE         → write the structured digest

Giving Claude pre-processed article text avoids the token-limit problem
that occurs when Claude has to echo back large JSON payloads in tool calls.

Output contract (strict JSON from Claude):
{
    "subject":   str,
    "themes":    [str, str, str],
    "items": [
        {
            "title":          str,
            "url":            str,
            "bullets":        [str, str, str],
            "why_it_matters": str
        }
    ],
    "html_body": str          # ignored — ai/email_template.py renders the HTML
}
"""

import json
import logging
import re
import time
from datetime import datetime, timezone

from ai import tools as T
from ai.llm_client import call_llm

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Guardrails
# ---------------------------------------------------------------------------
MAX_ARTICLES_PER_FEED  = 5       # articles kept per feed before dedup/rank
MAX_ARTICLE_TEXT_CHARS = 7_000   # characters of article body sent to Claude
AGENT_TIMEOUT_S        = 300     # wall-clock timeout for the whole run

# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = """You are an expert news curator. You will be given a list of \
today's news articles (title, URL, and body text). Your job is to write a \
high-quality daily digest.

Return ONLY a JSON object — no markdown fences, no prose before or after — \
that exactly matches this schema:

{
  "subject":   "Daily News Digest — YYYY-MM-DD",
  "themes":    ["theme 1", "theme 2", "theme 3"],
  "items": [
    {
      "title":          "Article headline",
      "url":            "https://...",
      "bullets":        ["key point 1", "key point 2", "key point 3"],
      "why_it_matters": "One crisp sentence on significance."
    }
  ],
  "html_body": ""
}

Rules:
- "themes"  — exactly 3 strings identifying the top recurring themes.
- "items"   — one entry per article provided; aim for all of them (up to 10).
- "bullets" — exactly 3 concise bullet points per article.
- "html_body" — leave as an empty string; the email template handles HTML.
- Write for a smart, busy reader. Be specific, not vague."""


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

def _extract_json(text: str) -> dict:
    """Extract the first valid JSON object from Claude's response text."""
    stripped = text.strip()

    # Fast path: whole response is JSON
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass

    # Strip markdown fences
    fenced = re.sub(r"^```(?:json)?\s*", "", stripped, flags=re.MULTILINE)
    fenced = re.sub(r"\s*```$", "", fenced, flags=re.MULTILINE)
    try:
        return json.loads(fenced.strip())
    except json.JSONDecodeError:
        pass

    # Last resort: grab outermost { ... }
    match = re.search(r"\{[\s\S]*\}", stripped)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass

    raise ValueError(
        "Claude's response did not contain a valid JSON object.\n"
        f"Preview: {text[:300]!r}"
    )


def _validate_output(data: dict) -> None:
    """Raise ValueError if the output contract is not satisfied."""
    for key in ("subject", "themes", "items", "html_body"):
        if key not in data:
            raise ValueError(f"Output contract violated: missing key {key!r}")

    themes = data["themes"]
    if not isinstance(themes, list) or not (1 <= len(themes) <= 5):
        raise ValueError(f"'themes' must be a list of 1–5 strings, got {themes!r}")

    items = data["items"]
    if not isinstance(items, list) or len(items) < 1:
        raise ValueError("'items' must be a non-empty list")

    for idx, item in enumerate(items):
        for field in ("title", "url", "bullets", "why_it_matters"):
            if field not in item:
                raise ValueError(f"items[{idx}] missing field {field!r}")
        if not isinstance(item["bullets"], list) or len(item["bullets"]) < 1:
            raise ValueError(f"items[{idx}]['bullets'] must be a non-empty list")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run_agent(
    feeds: list,
    recipient: str,
    provider: str = "gemini",
    model: str = "gemini-2.0-flash",
    max_per_feed: int = MAX_ARTICLES_PER_FEED,
) -> dict:
    """
    Run the news digest pipeline and return a validated output dict.

    Steps 1–3 run entirely in Python. Step 4 makes a single LLM API call.

    Parameters
    ----------
    feeds : list[str]
        RSS feed URLs to aggregate.
    recipient : str
        Destination email address (used in the user message for context).
    provider : str
        LLM provider: "gemini", "anthropic", or "openai".
    model : str
        Model ID appropriate for the provider.
    max_per_feed : int
        Max articles to keep per feed before dedup/rank.

    Returns
    -------
    dict  with keys: subject, themes, items, html_body
    """
    start = time.monotonic()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # ── Step 1: Fetch RSS feeds ────────────────────────────────────────────
    logger.info(f"Step 1/4 — Fetching {len(feeds)} RSS feed(s) (max {max_per_feed}/feed)")
    all_articles: list = []
    for feed_url in feeds:
        try:
            articles = T.fetch_rss(feed_url)
            kept = articles[:max_per_feed]
            all_articles.extend(kept)
            logger.info(f"  {feed_url}: {len(articles)} entries → kept {len(kept)}")
        except Exception as exc:
            logger.warning(f"  Skipping feed {feed_url}: {exc}")

    if not all_articles:
        raise RuntimeError("No articles collected from any feed.")

    # ── Step 2: Dedupe + rank ──────────────────────────────────────────────
    logger.info("Step 2/4 — Deduplicating and ranking")
    deduped = T.dedupe(all_articles)
    ranked  = T.rank(deduped, top_k=10)
    logger.info(f"  {len(all_articles)} → {len(deduped)} after dedup → top {len(ranked)}")

    # ── Step 3: Fetch article text ─────────────────────────────────────────
    logger.info("Step 3/4 — Fetching article text")
    for art in ranked:
        result = T.fetch_article_text(art["url"])
        text   = result.get("text") or ""
        if len(text) > MAX_ARTICLE_TEXT_CHARS:
            text = text[:MAX_ARTICLE_TEXT_CHARS] + " … [truncated]"
        art["text"] = text

    with_text = sum(1 for a in ranked if a.get("text"))
    logger.info(f"  {with_text}/{len(ranked)} articles have extractable text")

    # ── Step 4: Single LLM call to write the digest ───────────────────────
    logger.info(f"Step 4/4 — Calling {provider}/{model} to write digest")

    elapsed = time.monotonic() - start
    if elapsed > AGENT_TIMEOUT_S:
        raise TimeoutError(f"Pipeline timed out before Claude call ({elapsed:.0f}s)")

    article_blocks = []
    for i, art in enumerate(ranked, 1):
        body = art.get("text") or "(article text unavailable — use title only)"
        article_blocks.append(
            f"=== Article {i} ===\n"
            f"Title: {art['title']}\n"
            f"URL:   {art['url']}\n\n"
            f"{body}"
        )

    user_message = (
        f"Today is {today}. "
        f"Write a digest for the {len(ranked)} articles below. "
        f"It will be emailed to: {recipient}.\n\n"
        + "\n\n".join(article_blocks)
    )

    raw_text = call_llm(
        user_message=user_message,
        system=SYSTEM_PROMPT,
        provider=provider,
        model=model,
        max_tokens=8192,
    )

    output = _extract_json(raw_text)
    _validate_output(output)

    elapsed = time.monotonic() - start
    logger.info(
        f"Done — {len(output['items'])} items in digest, "
        f"{elapsed:.1f}s total, provider={provider} model={model}"
    )
    return output
