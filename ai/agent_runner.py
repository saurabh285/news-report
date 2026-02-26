"""
ai/agent_runner.py — Bounded tool-calling agent loop.

The agent is given four tools (fetch_rss, fetch_article_text, dedupe, rank).
It calls them in whatever order it chooses, then returns a final JSON object
that satisfies the output contract below.

Output contract (strict JSON from Claude):
{
    "subject":   str,
    "themes":    [str, str, str],           # exactly 3 themes
    "items":     [                          # 1–10 articles
        {
            "title":          str,
            "url":            str,
            "bullets":        [str, str, str],
            "why_it_matters": str
        }
    ],
    "html_body": str                        # full HTML email body
}

Guardrails
----------
MAX_TOOL_CALLS       = 30   hard cap on total tool invocations
MAX_ARTICLES_TO_FETCH = 40  fetch_article_text is skipped after this many calls
AGENT_TIMEOUT_S      = 300  wall-clock seconds before TimeoutError
"""

import json
import logging
import re
import time
from datetime import datetime, timezone

from ai import tools as T
from ai.claude_client import call_claude

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Guardrails
# ---------------------------------------------------------------------------
MAX_TOOL_CALLS = 30
MAX_ARTICLES_TO_FETCH = 40
AGENT_TIMEOUT_S = 300  # 5 minutes

# Article text is truncated to this many characters before being returned to
# Claude.  Keeping this well below 8 000 chars keeps per-call token costs low
# while still giving the model enough context to write meaningful bullets.
MAX_ARTICLE_TEXT_CHARS = 7_000

# ---------------------------------------------------------------------------
# Tool schemas (JSON Schema format required by the Anthropic API)
# ---------------------------------------------------------------------------
TOOL_SCHEMAS: list = [
    {
        "name": "fetch_rss",
        "description": (
            "Fetch and parse an RSS or Atom feed URL. "
            "Returns a list of article objects each containing: "
            "title (str), url (str), published_ts (int Unix timestamp), source (str)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "source_url": {
                    "type": "string",
                    "description": "The RSS/Atom feed URL to fetch.",
                }
            },
            "required": ["source_url"],
        },
    },
    {
        "name": "fetch_article_text",
        "description": (
            "Download a news article URL and extract its readable text content. "
            "Returns {url, text, extracted_ok}. "
            "Call this only for articles you intend to include in the digest."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "The article URL to download and extract.",
                }
            },
            "required": ["url"],
        },
    },
    {
        "name": "dedupe",
        "description": (
            "Remove duplicate articles by canonicalized URL "
            "(also strips common tracking parameters such as utm_*). "
            "Pass the full combined list; receive back a deduplicated list."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "description": "List of article objects that must each have a 'url' field.",
                    "items": {"type": "object"},
                }
            },
            "required": ["items"],
        },
    },
    {
        "name": "rank",
        "description": (
            "Rank article objects by recency-decay score (most recent first) "
            "and return the top top_k articles. "
            "Each article must have a 'published_ts' (Unix timestamp int) field."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "description": "List of article objects to rank.",
                    "items": {"type": "object"},
                },
                "top_k": {
                    "type": "integer",
                    "description": "How many top articles to return (default 10).",
                    "default": 10,
                },
            },
            "required": ["items"],
        },
    },
]

# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = """You are an expert news curator agent. Your job is to produce a \
high-quality daily news digest email.

Workflow
--------
1. Call fetch_rss once for each feed URL the user provides.
2. Merge all returned article lists into one combined list.
3. Call dedupe on the combined list to remove duplicates.
4. Call rank with top_k=10 to get the 10 most-recent articles.
5. For each of those 10 articles call fetch_article_text to get the full text.
6. Synthesise the content and compose the digest.

Final output
------------
When you are ready, respond with ONLY a JSON object — no markdown fences, \
no prose before or after it — that exactly matches this schema:

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
  "html_body": "<html>...</html>"
}

Rules:
- "themes" must be exactly 3 strings identifying the top recurring themes.
- "items" must contain between 5 and 10 objects (aim for 10).
- Each item must have exactly 3 bullets.
- "html_body" must be a complete, self-contained HTML document styled for \
  email clients (inline CSS only, no external resources). \
  Include a header with today's date, the three themes as a short list, \
  then each article as a card with its title (linked), why_it_matters in \
  italics, and the three bullets.
"""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _execute_tool(name: str, args: dict, article_fetch_count: list) -> str:
    """Dispatch a tool call and return the result serialised as a JSON string."""
    try:
        if name == "fetch_rss":
            result = T.fetch_rss(args["source_url"])

        elif name == "fetch_article_text":
            if article_fetch_count[0] >= MAX_ARTICLES_TO_FETCH:
                result = {
                    "url": args.get("url", ""),
                    "text": "",
                    "extracted_ok": False,
                    "error": (
                        f"Skipped: MAX_ARTICLES_TO_FETCH ({MAX_ARTICLES_TO_FETCH}) reached. "
                        "Use text from RSS summary or title instead."
                    ),
                }
            else:
                result = T.fetch_article_text(args["url"])
                article_fetch_count[0] += 1
                # Truncate article text to keep token costs low for cheap models
                if result.get("text") and len(result["text"]) > MAX_ARTICLE_TEXT_CHARS:
                    result = {
                        **result,
                        "text": result["text"][:MAX_ARTICLE_TEXT_CHARS] + " … [truncated]",
                    }
                    logger.debug(
                        f"  Article text truncated to {MAX_ARTICLE_TEXT_CHARS} chars"
                    )

        elif name == "dedupe":
            result = T.dedupe(args["items"])

        elif name == "rank":
            result = T.rank(args["items"], args.get("top_k", 10))

        else:
            result = {"error": f"Unknown tool: {name!r}"}

        return json.dumps(result, default=str)

    except Exception as exc:
        logger.error(f"Tool '{name}' raised an exception: {exc}")
        return json.dumps({"error": str(exc)})


def _extract_json(text: str) -> dict:
    """
    Extract the first valid top-level JSON object from ``text``.

    Tries the whole string first, then falls back to a regex search for
    the outermost ``{...}`` block.
    """
    stripped = text.strip()
    # Fast path: the whole response is already JSON
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass

    # Strip optional markdown code fences
    fenced = re.sub(r"^```(?:json)?\s*", "", stripped, flags=re.MULTILINE)
    fenced = re.sub(r"\s*```$", "", fenced, flags=re.MULTILINE)
    try:
        return json.loads(fenced.strip())
    except json.JSONDecodeError:
        pass

    # Last resort: grab the outermost {...}
    match = re.search(r"\{[\s\S]*\}", stripped)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass

    raise ValueError(
        "Claude's final response did not contain a valid JSON object.\n"
        f"Response preview: {text[:300]!r}"
    )


def _validate_output(data: dict) -> None:
    """Raise ValueError if the output contract is not satisfied."""
    required_keys = ("subject", "themes", "items", "html_body")
    for key in required_keys:
        if key not in data:
            raise ValueError(f"Output contract violated: missing key {key!r}")

    themes = data["themes"]
    if not isinstance(themes, list) or not (1 <= len(themes) <= 5):
        raise ValueError(
            f"'themes' must be a list of 1–5 strings, got {themes!r}"
        )

    items = data["items"]
    if not isinstance(items, list) or not (1 <= len(items) <= 10):
        raise ValueError(
            f"'items' must be a list of 1–10 article dicts, got {len(items) if isinstance(items, list) else type(items)}"
        )

    for idx, item in enumerate(items):
        for field in ("title", "url", "bullets", "why_it_matters"):
            if field not in item:
                raise ValueError(f"items[{idx}] is missing field {field!r}")
        bullets = item["bullets"]
        if not isinstance(bullets, list) or not (1 <= len(bullets) <= 5):
            raise ValueError(
                f"items[{idx}]['bullets'] must be a list of 1–5 strings"
            )

    if not data.get("html_body", "").strip():
        raise ValueError("'html_body' must not be empty")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run_agent(
    feeds: list,
    recipient: str,
    model: str = "claude-3-5-haiku-20241022",
) -> dict:
    """
    Run the bounded agent loop and return a validated output dict.

    Parameters
    ----------
    feeds : list[str]
        RSS feed URLs to aggregate.
    recipient : str
        Email address the digest will be sent to (passed to Claude for context).
    model : str
        Claude model ID to use.

    Returns
    -------
    dict
        Validated output matching the contract (subject, themes, items, html_body).

    Raises
    ------
    RuntimeError
        When ANTHROPIC_API_KEY is missing or the anthropic package is absent.
    TimeoutError
        When the agent loop exceeds AGENT_TIMEOUT_S seconds.
    ValueError
        When Claude's final output fails contract validation.
    """
    start_time = time.monotonic()
    tool_calls_made = 0
    article_fetch_count = [0]  # mutable list so _execute_tool can mutate it

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    user_message = (
        f"Today is {today}. "
        f"Create a top-10 daily news digest from these RSS feeds: {feeds}. "
        f"The digest will be emailed to: {recipient}."
    )

    messages: list = [{"role": "user", "content": user_message}]

    logger.info(
        f"Agent loop starting — model={model}, "
        f"MAX_TOOL_CALLS={MAX_TOOL_CALLS}, "
        f"MAX_ARTICLES_TO_FETCH={MAX_ARTICLES_TO_FETCH}, "
        f"timeout={AGENT_TIMEOUT_S}s"
    )

    while True:
        # ── Guardrail: wall-clock timeout ──────────────────────────────────
        elapsed = time.monotonic() - start_time
        if elapsed > AGENT_TIMEOUT_S:
            raise TimeoutError(
                f"Agent timed out after {elapsed:.0f}s "
                f"(limit {AGENT_TIMEOUT_S}s)."
            )

        # ── Guardrail: tool-call budget ─────────────────────────────────────
        if tool_calls_made >= MAX_TOOL_CALLS:
            raise RuntimeError(
                f"Agent exceeded MAX_TOOL_CALLS={MAX_TOOL_CALLS} without "
                "producing a final answer."
            )

        # ── Call Claude ─────────────────────────────────────────────────────
        response = call_claude(
            messages=messages,
            tools=TOOL_SCHEMAS,
            system=SYSTEM_PROMPT,
            model=model,
            max_tokens=4096,
        )

        stop_reason = response.stop_reason
        logger.info(
            f"  Claude turn: stop_reason={stop_reason!r}, "
            f"tool_calls_so_far={tool_calls_made}, "
            f"articles_fetched={article_fetch_count[0]}, "
            f"elapsed={elapsed:.1f}s"
        )

        # ── End turn: parse and validate final JSON ──────────────────────────
        if stop_reason == "end_turn":
            text_parts = [
                block.text
                for block in response.content
                if hasattr(block, "text")
            ]
            full_text = "\n".join(text_parts)
            logger.info("Agent returned end_turn — parsing output JSON")
            output = _extract_json(full_text)
            _validate_output(output)
            logger.info(
                f"Agent completed successfully: "
                f"{len(output['items'])} items, "
                f"{tool_calls_made} tool calls, "
                f"{elapsed:.1f}s elapsed"
            )
            return output

        # ── Tool use: execute each tool call, collect results ───────────────
        if stop_reason == "tool_use":
            tool_results: list = []
            for block in response.content:
                if not hasattr(block, "type") or block.type != "tool_use":
                    continue

                tool_calls_made += 1
                name: str = block.name
                args: dict = block.input
                tool_id: str = block.id

                logger.info(
                    f"  → Tool #{tool_calls_made}: {name}"
                    f"({', '.join(f'{k}={str(v)[:60]!r}' for k, v in args.items())})"
                )

                result_str = _execute_tool(name, args, article_fetch_count)

                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": tool_id,
                        "content": result_str,
                    }
                )

            # Append assistant turn and tool results before looping
            messages.append({"role": "assistant", "content": response.content})
            messages.append({"role": "user", "content": tool_results})
            continue

        # ── Unexpected stop reason ───────────────────────────────────────────
        raise RuntimeError(
            f"Unexpected stop_reason from Claude: {stop_reason!r}"
        )
