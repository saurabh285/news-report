"""
ai/email_template.py — Renders Claude's structured digest output into a
polished, email-client-compatible HTML email.

Claude is responsible for content (themes, bullets, why_it_matters).
This module is responsible for presentation — it owns all the HTML/CSS.

Usage
-----
    from ai.email_template import render_html
    html = render_html(agent_output)   # agent_output is the validated dict
"""

from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# Colour / style tokens  (change these to retheme the whole email)
# ---------------------------------------------------------------------------
_C = {
    "bg":            "#f0f2f5",
    "card_bg":       "#ffffff",
    "header_bg":     "#0f172a",   # deep navy
    "header_text":   "#f8fafc",
    "accent":        "#3b82f6",   # bright blue
    "accent_dark":   "#1d4ed8",
    "tag_bg":        "#dbeafe",
    "tag_text":      "#1e40af",
    "number_text":   "#3b82f6",
    "why_bg":        "#fefce8",   # pale yellow
    "why_border":    "#fde68a",
    "why_text":      "#92400e",
    "bullet_marker": "#3b82f6",
    "body_text":     "#1e293b",
    "muted":         "#64748b",
    "divider":       "#e2e8f0",
    "footer_bg":     "#f8fafc",
    "footer_text":   "#94a3b8",
}

_FONT = "font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _esc(text: str) -> str:
    """Minimal HTML escaping."""
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _theme_pill(theme: str) -> str:
    return (
        f'<span style="display:inline-block;margin:3px 4px;padding:5px 14px;'
        f'background:{_C["tag_bg"]};color:{_C["tag_text"]};border-radius:999px;'
        f'font-size:13px;font-weight:600;{_FONT}">'
        f'{_esc(theme)}</span>'
    )


def _bullet_row(text: str) -> str:
    return (
        f'<tr><td style="padding:3px 0 3px 0;vertical-align:top;width:18px;">'
        f'<span style="color:{_C["bullet_marker"]};font-size:16px;line-height:1;">&#x2022;</span>'
        f'</td>'
        f'<td style="padding:3px 0;color:{_C["body_text"]};font-size:15px;line-height:1.6;{_FONT}">'
        f'{_esc(text)}</td></tr>'
    )


def _article_card(index: int, item: dict) -> str:
    title        = _esc(item.get("title", "Untitled"))
    url          = _esc(item.get("url", "#"))
    why          = _esc(item.get("why_it_matters", ""))
    bullets      = item.get("bullets") or []

    bullet_rows  = "\n".join(_bullet_row(b) for b in bullets)

    why_block = ""
    if why:
        why_block = (
            f'<table width="100%" cellpadding="0" cellspacing="0" style="margin:12px 0;">'
            f'<tr><td style="background:{_C["why_bg"]};border-left:3px solid {_C["why_border"]};'
            f'border-radius:0 6px 6px 0;padding:10px 14px;">'
            f'<span style="color:{_C["why_text"]};font-size:14px;font-style:italic;{_FONT}">'
            f'<strong style="font-style:normal;">Why it matters &mdash;</strong> {why}'
            f'</span></td></tr></table>'
        )

    return f"""
<!--  ARTICLE CARD {index}  -->
<tr><td style="padding:0 0 20px 0;">
  <table width="100%" cellpadding="0" cellspacing="0"
         style="background:{_C["card_bg"]};border-radius:12px;
                border:1px solid {_C["divider"]};
                box-shadow:0 1px 3px rgba(0,0,0,.06);">
    <tr>
      <!-- left accent stripe -->
      <td width="4" style="background:{_C["accent"]};border-radius:12px 0 0 12px;">&nbsp;</td>
      <td style="padding:20px 24px;">

        <!-- number + title -->
        <table width="100%" cellpadding="0" cellspacing="0">
          <tr>
            <td width="36" style="vertical-align:top;padding-top:2px;">
              <span style="display:inline-block;width:28px;height:28px;line-height:28px;
                           text-align:center;background:{_C["accent"]};color:#fff;
                           border-radius:50%;font-size:13px;font-weight:700;{_FONT}">
                {index}
              </span>
            </td>
            <td style="vertical-align:top;">
              <a href="{url}" target="_blank"
                 style="color:{_C["body_text"]};text-decoration:none;
                        font-size:18px;font-weight:700;line-height:1.4;{_FONT}">
                {title}
              </a>
            </td>
          </tr>
        </table>

        <!-- why it matters -->
        {why_block}

        <!-- bullets -->
        <table cellpadding="0" cellspacing="0" style="margin:4px 0 12px 0;">
          {bullet_rows}
        </table>

        <!-- read more -->
        <a href="{url}" target="_blank"
           style="display:inline-block;padding:7px 16px;
                  background:{_C["accent"]};color:#fff;border-radius:6px;
                  font-size:13px;font-weight:600;text-decoration:none;{_FONT}">
          Read full article &rarr;
        </a>

      </td>
    </tr>
  </table>
</td></tr>"""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def render_html(output: dict) -> str:
    """
    Render a validated agent output dict into a complete HTML email string.

    Parameters
    ----------
    output : dict
        Validated dict from agent_runner.run_agent() with keys:
        subject, themes, items, html_body (html_body is ignored here).

    Returns
    -------
    str
        Self-contained HTML document ready to be sent as an HTML email.
    """
    today       = datetime.now(timezone.utc).strftime("%A, %B %-d, %Y")
    subject     = _esc(output.get("subject", f"Daily News Digest — {today}"))
    themes      = output.get("themes") or []
    items       = output.get("items") or []
    item_count  = len(items)

    theme_pills = "\n".join(_theme_pill(t) for t in themes)
    article_cards = "\n".join(_article_card(i + 1, item) for i, item in enumerate(items))

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>{subject}</title>
</head>
<body style="margin:0;padding:0;background:{_C["bg"]};{_FONT}">

<!-- OUTER WRAPPER -->
<table width="100%" cellpadding="0" cellspacing="0"
       style="background:{_C["bg"]};padding:32px 16px;">
<tr><td align="center">

<!-- INNER CONTAINER (max 620px) -->
<table width="620" cellpadding="0" cellspacing="0"
       style="max-width:620px;width:100%;">

  <!-- ── HEADER ── -->
  <tr><td style="background:{_C["header_bg"]};border-radius:16px 16px 0 0;
                 padding:36px 36px 28px 36px;">
    <table width="100%" cellpadding="0" cellspacing="0">
      <tr>
        <td>
          <div style="color:{_C["accent"]};font-size:11px;font-weight:700;
                      letter-spacing:2px;text-transform:uppercase;margin-bottom:8px;">
            Daily News Digest
          </div>
          <div style="color:{_C["header_text"]};font-size:26px;font-weight:800;
                      line-height:1.2;margin-bottom:6px;">
            {today}
          </div>
          <div style="color:#94a3b8;font-size:14px;">
            {item_count} top stor{"y" if item_count == 1 else "ies"} curated by AI
          </div>
        </td>
        <td align="right" style="vertical-align:top;">
          <div style="background:rgba(59,130,246,.15);border:1px solid rgba(59,130,246,.3);
                      border-radius:999px;padding:6px 14px;display:inline-block;">
            <span style="color:{_C["accent"]};font-size:12px;font-weight:600;">
              &#9679; Claude
            </span>
          </div>
        </td>
      </tr>
    </table>
  </td></tr>

  <!-- ── THEMES BAR ── -->
  <tr><td style="background:#1e293b;padding:16px 36px;">
    <div style="color:#94a3b8;font-size:11px;font-weight:700;
                letter-spacing:1.5px;text-transform:uppercase;margin-bottom:10px;">
      Today&rsquo;s themes
    </div>
    <div>
      {theme_pills}
    </div>
  </td></tr>

  <!-- ── ARTICLE CARDS ── -->
  <tr><td style="background:{_C["bg"]};padding:24px 24px 8px 24px;">
    <table width="100%" cellpadding="0" cellspacing="0">
      {article_cards}
    </table>
  </td></tr>

  <!-- ── FOOTER ── -->
  <tr><td style="background:{_C["footer_bg"]};border-top:1px solid {_C["divider"]};
                 border-radius:0 0 16px 16px;padding:20px 36px;text-align:center;">
    <p style="margin:0 0 4px 0;color:{_C["footer_text"]};font-size:12px;">
      Generated by <strong>Daily News Report Agent</strong>
      &mdash; powered by Claude AI
    </p>
    <p style="margin:0;color:{_C["footer_text"]};font-size:11px;">
      You are receiving this because you set up the Daily News Report Agent.
    </p>
  </td></tr>

</table>
<!-- /INNER CONTAINER -->

</td></tr>
</table>
<!-- /OUTER WRAPPER -->

</body>
</html>"""
