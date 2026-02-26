# Daily News Report Agent

Automatically fetches articles from RSS feeds, picks the 10 most recent stories,
summarises each one, and emails you a clean daily digest — no AI API required.

---

## How it works

1. Reads RSS feed URLs from `config.yaml`
2. Downloads each article page and extracts readable text (via [trafilatura](https://github.com/adbar/trafilatura))
3. Removes duplicate articles by URL
4. Ranks articles using a recency decay score (newest = highest score)
5. Selects the top 10 articles
6. Generates a 2–3 sentence extractive summary for each (pure Python, no ML needed)
7. Sends a formatted email via Gmail SMTP

---

## Quick start (local machine)

### Step 1 — Install Python

Download and install **Python 3.8 or later** from https://www.python.org/downloads/.
During installation on Windows, tick **"Add Python to PATH"**.

Verify it works by opening a terminal and running:
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

Open `config.yaml` in any text editor and:

1. Add or replace the RSS feed URLs under `feeds:`.
2. Set your email address as `email_recipient`.

```yaml
feeds:
  - https://rss.nytimes.com/services/xml/rss/nyt/HomePage.xml
  - https://feeds.bbci.co.uk/news/rss.xml
  # Add as many feeds as you like

email_recipient: you@example.com
```

### Step 6 — Create a Gmail App Password

> Regular Gmail passwords won't work. You need an **App Password**.

1. Go to your Google Account → **Security** → **2-Step Verification** (enable it if not already on).
2. Then go to **Security** → **App passwords**.
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

**Windows (PowerShell):**
```powershell
$env:GMAIL_USER="your@gmail.com"
$env:GMAIL_APP_PASSWORD="xxxx xxxx xxxx xxxx"
```

### Step 8 — Run the agent

```bash
python main.py
```

You should see log output as it fetches feeds, downloads articles, and sends the email.

---

## Automate with GitHub Actions (free, runs in the cloud)

The workflow file `.github/workflows/daily.yml` triggers every day at **07:00 UTC**.

### Setup

1. Push this repository to GitHub (if you haven't already).

2. Go to your repository on GitHub → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**.

3. Add these three secrets:

   | Secret name | Value |
   |-------------|-------|
   | `GMAIL_USER` | Your Gmail address |
   | `GMAIL_APP_PASSWORD` | The App Password from Step 6 |
   | `EMAIL_RECIPIENT` | Email address to send the report to |

4. That's it! The workflow runs automatically every day.
   You can also trigger it manually from **Actions** → **Daily News Report** → **Run workflow**.

---

## Logging and error handling

- All output is printed to the console with timestamps.
- Feed parse errors, download failures, and SMTP errors are logged and will **not** crash the script.
- If an article's text cannot be extracted it is skipped, so you always get the best available stories.

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `No email received` | Check `GMAIL_USER` / `GMAIL_APP_PASSWORD` are set correctly and that 2-Step Verification + App Passwords are enabled in your Google account |
| `Feed parse warning` | The URL may be slow or temporarily unavailable — the script continues with other feeds |
| `No articles with extractable text` | Some sites block scrapers; try adding different RSS sources in `config.yaml` |
| Script exits immediately | Make sure `email_recipient` is set in `config.yaml` or `EMAIL_RECIPIENT` env var |

---

## Project structure

```
news-report/
├── main.py                       # Main script
├── config.yaml                   # Your feed URLs and recipient email
├── requirements.txt              # Python dependencies
├── .github/
│   └── workflows/
│       └── daily.yml             # GitHub Actions scheduled job
└── README.md                     # This file
```

---

## Dependencies

| Package | Purpose |
|---------|---------|
| `feedparser` | Parse RSS / Atom feeds |
| `requests` | Download article web pages |
| `trafilatura` | Extract clean readable text from HTML |
| `PyYAML` | Read `config.yaml` |

No paid APIs, no ML models, no database required.
