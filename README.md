# Daily News Report Agent

Automatically fetches articles from RSS feeds, picks the 10 most recent
stories, summarises each one, and emails you a clean daily digest.

Two pipelines are available — switch between them with a single line in
`config.yaml`:

| `ai.mode`           | What it does | API key needed? |
|---------------------|-------------|-----------------|
| `free` (default)    | Deterministic extractive pipeline — pure Python, no external AI | No |
| `agent` / `llm`      | Tool-calling a cloud LLM for richer output | Yes (see below)

Agent mode is provider‑agnostic; you can choose Gemini, Claude, OpenAI, or
any other backend the code supports.  The provider is selected in priority
order from:

1. `LLM_PROVIDER` environment variable (`gemini`, `anthropic`, `openai`)
2. `ai.provider` in `config.yaml`
3. Auto‑detect from which API key is set (`GEMINI_API_KEY`,
   `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`)

If no provider can be determined, the program exits with a helpful error
message.  The `agent` pipeline will fall back automatically to free mode if
the configured provider fails or the API key is missing.

Agent mode **falls back automatically** to free mode if the API key is
missing or if the agent errors or times out.

---

## How it works

### Free mode
1. Reads RSS feed URLs from `config.yaml`
2. Downloads each article and extracts clean text ([trafilatura](https://github.com/adbar/trafilatura))
3. Removes duplicate articles by URL
4. Ranks articles using a recency-decay score (newest = highest)
5. Selects the top 10
6. Generates a 2–3 sentence extractive summary per article (pure Python, no ML)
7. Sends a plain-text email via Gmail SMTP

### Agent mode
1. Claude receives the list of feed URLs as a goal
2. Claude calls tools (`fetch_rss`, `fetch_article_text`, `dedupe`, `rank`) to gather and process articles
3. Claude writes the digest (subject line, 3 recurring themes, 10 articles each with 3 bullets + *why it matters*, HTML body)
4. The runner validates the output contract and sends an HTML email

Guardrails prevent runaway API usage:
- Max 30 tool calls per run
- Max 40 article-text fetches
- 5-minute wall-clock timeout
- Any failure → automatic fallback to free mode

---

## Quick start (local machine)

### Step 1 — Install Python

Download **Python 3.8 or later** from https://www.python.org/downloads/.
On Windows, tick **"Add Python to PATH"** during installation.

Verify with:
```
python --version
```

### Step 2 — Get the project

```bash
git clone <this-repo-url>
cd news-report
```

Or download the ZIP from GitHub and unzip it.

### Step 3 — Create a virtual environment (recommended)

```bash
python -m venv venv
```

Activate it:

| Platform | Command |
|----------|---------|
| macOS / Linux | `source venv/bin/activate` |
| Windows (cmd) | `venv\Scripts\activate.bat` |
| Windows (PowerShell) | `venv\Scripts\Activate.ps1` |

### Step 4 — Install dependencies

```bash
pip install -r requirements.txt
```

### Step 5 — Edit `config.yaml`

Open `config.yaml` in any text editor:

```yaml
feeds:
  - https://rss.nytimes.com/services/xml/rss/nyt/HomePage.xml
  - https://feeds.bbci.co.uk/news/rss.xml
  # Add as many feeds as you like

email_recipient: you@example.com

ai:
  mode: free   # change to "agent" (or omit for "agent") to enable LLM/agent mode
  # provider: gemini  # only relevant when mode is agent (see docs above)
```
### Step 6 — Create a Gmail App Password

> Regular Gmail passwords won't work. You need an **App Password**.

1. Go to your Google Account → **Security** → enable **2-Step Verification**.
2. Then **Security** → **App passwords**.
3. Choose app: **Mail**, device: **Other** (name it "News Agent").
4. Copy the 16-character password shown.

Reference: https://support.google.com/accounts/answer/185833

### Step 7 — Set environment variables

**macOS / Linux:**
```bash
export GMAIL_USER="your@gmail.com"
export GMAIL_APP_PASSWORD="xxxx xxxx xxxx xxxx"
```

**Windows (Command Prompt):**
```cmd
set GMAIL_USER=your@gmail.com
set GMAIL_APP_PASSWORD=xxxx xxxx xxxx xxxx
```

### Step 8 — Run

```bash
python main.py
```

---

## Agent mode (optional)

Agent mode uses a cloud LLM via the provider‑agnostic interface defined in
`ai/llm_client.py`.  At the time of writing the supported providers are
**gemini** (Google Gemini free tier), **anthropic** (Claude), and **openai**
(GPT).  The program automatically selects a provider based on the
following priority:

1. `LLM_PROVIDER` environment variable (`gemini`, `anthropic`, `openai`)
2. `ai.provider` in `config.yaml`
3. Auto‑detect from whichever API key is present (`GEMINI_API_KEY`,
   `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`)

If no provider can be determined the run will abort with a helpful error.
When multiple keys are set the ordering above is used, so you can override
via `LLM_PROVIDER` even if multiple credentials exist.

The LLM call returns structured JSON which the agent runner converts into a
polished HTML digest.  Compared with free mode you get:

- A curated subject line
- 3 recurring **themes** across the day's stories
- Per-article **bullet points** and a *why it matters* sentence
- A fully formatted **HTML email**

### Choosing a model

Model resolution follows this priority (per provider):

| Source | How to set |
|--------|------------|
| `{PROVIDER}_MODEL` env var | `export GEMINI_MODEL=...` (or ANTHROPIC_MODEL/OPENAI_MODEL) |
| `ai.model` in `config.yaml` | `model: gpt-4o-mini` etc. |
| Built-in default | cheapest free-tier model for the provider |

For example, to force Gemini even if an Anthropic key exists:

```bash
export LLM_PROVIDER=gemini
export GEMINI_API_KEY=...
```

And in `config.yaml` you can still specify a model name if you like.

### Requirements

1. An API key for your chosen provider:
   * `GEMINI_API_KEY` for Gemini (free).
   * `ANTHROPIC_API_KEY` for Claude.
   * `OPENAI_API_KEY` for OpenAI.
2. Set `ai.mode` to `agent` (or `llm`) in `config.yaml`:

```yaml
ai:
  mode: agent
```

3. (Optional) override provider or model via environment variables as
   described above.

4. Run:

```bash
python main.py
```

(Optional) run the test suite with `pytest` after installing the development
requirements.  The tests exercise configuration and error handling logic.
If the provider-specific package is missing or the key is unset, or if the
LLM call fails due to quota/rate‑limit errors (e.g. 429 from Gemini free tier),
the code logs a clear message and the pipeline falls back to the free mode
automatically.  The free-mode output is always rendered with the same HTML
template so the email still looks polished; when the agent is unavailable a
banner is added to the subject line and the themes bar points out the
fallback.
Or override at runtime without touching `config.yaml`:

```bash
export ANTHROPIC_MODEL="claude-sonnet-4-6"
python main.py
```

### What happens if the API key is missing

- If `ANTHROPIC_API_KEY` is not set **and** `ai.mode` is `free` → runs normally, no issue.
- If `ANTHROPIC_API_KEY` is not set **and** `ai.mode` is `agent` → logs a clear error, **automatically falls back to free mode**, and still sends the digest.

You can safely commit `ai.mode: agent` to your repository; the workflow will
use free mode until you add the `ANTHROPIC_API_KEY` secret in GitHub.

### GitHub Actions — adding the API key

Go to your repo → **Settings → Secrets and variables → Actions** → **New repository secret**:

| Secret name | Required | Value |
|-------------|----------|-------|
| `ANTHROPIC_API_KEY` | Yes (for agent mode) | Your Anthropic API key |
| `ANTHROPIC_MODEL` | No | Override model, e.g. `claude-haiku-3-5` |

---

## Automate with GitHub Actions (free, runs in the cloud)

The workflow `.github/workflows/daily.yml` runs every day at **07:00 UTC**.

### Setup

1. Push this repository to GitHub.
2. Add secrets at **Settings → Secrets and variables → Actions**:

| Secret | Required | Value |
|--------|----------|-------|
| `GMAIL_USER` | Yes | Your Gmail address |
| `GMAIL_APP_PASSWORD` | Yes | App Password from Step 6 |
| `EMAIL_RECIPIENT` | Yes | Address to receive the digest |
| `ANTHROPIC_API_KEY` | Only for agent mode | Your Anthropic API key |
| `ANTHROPIC_MODEL` | No | Override model (e.g. `claude-haiku-3-5`) |

3. Done. The workflow also runs a **smoke test** (`python -m py_compile`) on
   every execution to catch import errors before the script runs.

You can trigger it manually any time via **Actions → Daily News Report → Run workflow**.

---

## Project structure

```
news-report/
├── main.py                        # Entry point — routes by ai.mode
├── config.yaml                    # Feeds, recipient, ai.mode
├── requirements.txt               # Python dependencies
├── ai/
│   ├── __init__.py
│   ├── tools.py                   # fetch_rss, fetch_article_text, dedupe, rank, send_email_html
│   ├── claude_client.py           # Anthropic Messages API wrapper
│   └── agent_runner.py            # Bounded agent loop + output validation
├── .github/
│   └── workflows/
│       └── daily.yml              # Cron job + smoke test
└── README.md
```

---

## Dependencies

| Package | Purpose |
|---------|---------|
| `feedparser` | Parse RSS / Atom feeds |
| `requests` | Download article pages |
| `trafilatura` | Extract clean text from HTML |
| `PyYAML` | Read `config.yaml` |
| `anthropic` | Claude API client (agent mode only) |

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| No email received | Check `GMAIL_USER` / `GMAIL_APP_PASSWORD` are correct and App Passwords is enabled |
| Feed parse warning | Feed may be slow or temporarily down — other feeds still run |
| No articles with extractable text | Some sites block scrapers; add different RSS sources |
| Agent mode immediately falls back | Check `ANTHROPIC_API_KEY` is set and valid |
| `anthropic` package not found | Run `pip install anthropic` |
| Script exits immediately | Ensure `email_recipient` is set in `config.yaml` or `EMAIL_RECIPIENT` env var |
