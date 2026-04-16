#!/usr/bin/env python3
"""
The Mold Report — Weekly Newsletter Generator
================================================
Generates a Substack-ready newsletter from this week's published articles.
No API calls. Zero cost. Just formats what's already in articles.json.

Output: newsletter.html (paste into Substack's editor)

Usage:
  python generate_newsletter.py              # This week's newsletter
  python generate_newsletter.py --days 14    # Last 14 days
  python generate_newsletter.py --preview    # Print to stdout instead of file
"""

import json
import argparse
from datetime import datetime, timezone, timedelta
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
ARTICLES_FILE = SCRIPT_DIR / "articles.json"
OUTPUT_FILE = SCRIPT_DIR / "newsletter.html"

# Site URL for article links
SITE_URL = "https://jlpxmoldco.github.io/the-mold-report"


def load_articles():
    if ARTICLES_FILE.exists():
        with open(ARTICLES_FILE) as f:
            return json.load(f)
    return {"articles": []}


def get_week_articles(data, days=7):
    """Get published articles from the last N days, sorted by date."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    articles = []
    for a in data.get("articles", []):
        if a.get("status") != "published":
            continue
        try:
            pub = datetime.fromisoformat(a["publishedAt"].replace("Z", "+00:00"))
            if pub >= cutoff:
                articles.append(a)
        except (ValueError, KeyError):
            continue
    # Newest first
    articles.sort(key=lambda a: a.get("publishedAt", ""), reverse=True)
    return articles


def pick_top_stories(articles, n=3):
    """Pick the best stories for the lead section."""
    # Featured articles first, then by recency
    featured = [a for a in articles if a.get("featured")]
    rest = [a for a in articles if not a.get("featured")]
    return (featured + rest)[:n]


def pick_research(articles, n=3):
    """Pick research articles for the research section."""
    research = [a for a in articles if a.get("category") == "research"]
    return research[:n]


def pick_quick_hits(articles, already_used_ids, n=5):
    """Pick remaining articles for quick hits."""
    return [a for a in articles if a["id"] not in already_used_ids][:n]


def format_date_range(days=7):
    """Format the date range for the newsletter header."""
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    if start.month == end.month:
        return f"{start.strftime('%B %d')} – {end.strftime('%d, %Y')}"
    return f"{start.strftime('%B %d')} – {end.strftime('%B %d, %Y')}"


def truncate(text, length=180):
    """Truncate text cleanly at a sentence or word boundary."""
    if len(text) <= length:
        return text
    # Try to cut at a sentence
    for end in [". ", "? ", "! "]:
        idx = text[:length].rfind(end)
        if idx > length * 0.5:
            return text[:idx + 1]
    # Fall back to word boundary
    idx = text[:length].rfind(" ")
    return text[:idx] + "..." if idx > 0 else text[:length] + "..."


def category_label(cat):
    labels = {
        "research": "Research",
        "regulation": "Regulation",
        "news": "News",
        "industry": "Industry",
        "diagnostics": "Diagnostics",
    }
    return labels.get(cat, "News")


def generate_newsletter(days=7):
    """Generate the newsletter HTML."""
    data = load_articles()
    articles = get_week_articles(data, days=days)

    if not articles:
        print("No articles published in the last {days} days. Nothing to send.")
        return None

    date_range = format_date_range(days)
    total_count = len(articles)

    # Pick sections
    top_stories = pick_top_stories(articles, n=3)
    research = pick_research(articles, n=3)

    used_ids = {a["id"] for a in top_stories} | {a["id"] for a in research}
    quick_hits = pick_quick_hits(articles, used_ids, n=5)

    # Count by category for the intro
    cats = {}
    for a in articles:
        c = a.get("category", "news")
        cats[c] = cats.get(c, 0) + 1

    # --- BUILD HTML ---

    # Editor's intro (rotates based on week number)
    week_num = datetime.now().isocalendar()[1]
    intros = [
        f"Your AI editors processed {total_count} stories this week. Here's what made the cut.",
        f"{total_count} articles came through the pipeline this week. Nine AI editors argued about them. These survived.",
        f"The pipeline reviewed {total_count} stories. Most didn't make it. These did.",
        f"Another week, another {total_count} stories through the gauntlet. Here's what's worth your time.",
    ]
    intro = intros[week_num % len(intros)]

    # Top stories HTML
    top_html = ""
    for a in top_stories:
        link = f"{SITE_URL}/a/{a['id']}.html"
        summary = truncate(a["summary"], 200)
        source = a.get("source", "")
        cat = category_label(a.get("category", "news"))
        editors_note = ""
        if a.get("editorsNote"):
            editors_note = f'''
            <p style="background:#F5F0E8;border-left:3px solid #1B4D3E;padding:10px 14px;margin:10px 0 0;font-size:13px;color:#555;">
              <strong style="color:#1B4D3E;font-size:11px;text-transform:uppercase;letter-spacing:0.04em;">Editor's Note</strong><br>
              {a["editorsNote"]}
            </p>'''

        top_html += f'''
        <div style="margin-bottom:28px;padding-bottom:28px;border-bottom:1px solid #E5E4E0;">
          <p style="font-size:12px;color:#1B4D3E;font-weight:600;text-transform:uppercase;letter-spacing:0.05em;margin:0 0 6px;">{cat} · {source}</p>
          <h2 style="font-family:Georgia,serif;font-size:22px;font-weight:400;margin:0 0 10px;line-height:1.3;">
            <a href="{link}" style="color:#111;text-decoration:none;">{a["title"]}</a>
          </h2>
          <p style="font-size:15px;line-height:1.65;color:#555;margin:0;">{summary}</p>
          {editors_note}
          <p style="margin:10px 0 0;"><a href="{link}" style="color:#1B4D3E;font-size:14px;font-weight:500;text-decoration:none;">Read full story →</a></p>
        </div>'''

    # Research section HTML
    research_html = ""
    if research:
        for a in research:
            link = f"{SITE_URL}/a/{a['id']}.html"
            summary = truncate(a["summary"], 140)
            source = a.get("source", "")
            research_html += f'''
            <div style="margin-bottom:18px;padding-bottom:18px;border-bottom:1px solid #EEEDE9;">
              <h3 style="font-family:Georgia,serif;font-size:17px;font-weight:400;margin:0 0 6px;line-height:1.3;">
                <a href="{link}" style="color:#111;text-decoration:none;">{a["title"]}</a>
              </h3>
              <p style="font-size:14px;line-height:1.6;color:#555;margin:0 0 6px;">{summary}</p>
              <p style="font-size:12px;color:#999;margin:0;">{source}</p>
            </div>'''

    # Quick hits HTML
    quick_html = ""
    if quick_hits:
        for a in quick_hits:
            link = f"{SITE_URL}/a/{a['id']}.html"
            cat = category_label(a.get("category", "news"))
            quick_html += f'''
            <li style="margin-bottom:10px;font-size:14px;line-height:1.5;color:#555;">
              <a href="{link}" style="color:#111;text-decoration:none;font-weight:500;">{a["title"]}</a>
              <span style="color:#999;"> · {cat}</span>
            </li>'''

    # Category summary for intro
    cat_parts = []
    for cat_key in ["research", "regulation", "news", "industry", "diagnostics"]:
        if cat_key in cats:
            cat_parts.append(f"{cats[cat_key]} {category_label(cat_key).lower()}")
    cat_summary = ", ".join(cat_parts) if cat_parts else f"{total_count} stories"

    # --- FULL NEWSLETTER ---
    html = f'''<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"></head>
<body style="margin:0;padding:0;background:#F8F7F4;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;">

<div style="max-width:600px;margin:0 auto;padding:40px 24px;">

  <!-- HEADER -->
  <div style="text-align:center;margin-bottom:32px;">
    <h1 style="font-family:Georgia,serif;font-size:28px;font-weight:400;margin:0 0 4px;color:#111;">The Mold <em>Report</em></h1>
    <p style="font-size:13px;color:#999;margin:0;">Weekly · {date_range}</p>
  </div>

  <!-- EDITOR'S INTRO -->
  <div style="background:#FFFFFF;border:1px solid #E5E4E0;border-radius:8px;padding:20px 24px;margin-bottom:32px;">
    <p style="font-size:15px;line-height:1.65;color:#333;margin:0;">
      {intro}
    </p>
    <p style="font-size:13px;color:#999;margin:10px 0 0;">This week: {cat_summary}.</p>
  </div>

  <!-- TOP STORIES -->
  <div style="margin-bottom:12px;">
    <h2 style="font-family:Georgia,serif;font-size:14px;font-weight:400;color:#1B4D3E;text-transform:uppercase;letter-spacing:0.08em;margin:0 0 20px;padding-bottom:8px;border-bottom:2px solid #1B4D3E;">Top Stories</h2>
    {top_html}
  </div>

  <!-- RESEARCH ROUNDUP -->
  {f"""<div style="margin-bottom:12px;">
    <h2 style="font-family:Georgia,serif;font-size:14px;font-weight:400;color:#1B4D3E;text-transform:uppercase;letter-spacing:0.08em;margin:0 0 20px;padding-bottom:8px;border-bottom:2px solid #1B4D3E;">Research Roundup</h2>
    {research_html}
  </div>""" if research_html else ""}

  <!-- QUICK HITS -->
  {f"""<div style="margin-bottom:32px;">
    <h2 style="font-family:Georgia,serif;font-size:14px;font-weight:400;color:#1B4D3E;text-transform:uppercase;letter-spacing:0.08em;margin:0 0 20px;padding-bottom:8px;border-bottom:2px solid #1B4D3E;">Quick Hits</h2>
    <ul style="padding-left:20px;margin:0;">
      {quick_html}
    </ul>
  </div>""" if quick_html else ""}

  <!-- SITE CTA -->
  <div style="text-align:center;margin-bottom:32px;padding:24px;background:#E6F0EB;border-radius:8px;">
    <p style="font-size:15px;color:#333;margin:0 0 12px;">Read every story on the site. No paywall, no login.</p>
    <a href="{SITE_URL}" style="display:inline-block;background:#1B4D3E;color:#fff;padding:10px 24px;border-radius:6px;font-size:14px;font-weight:600;text-decoration:none;">Visit The Mold Report →</a>
  </div>

  <!-- MOLDCO FOOTER -->
  <div style="border-top:1px solid #E5E4E0;padding-top:24px;text-align:center;">
    <p style="font-size:13px;color:#999;margin:0 0 8px;">The Mold Report is published by the team behind <a href="https://www.moldco.com" style="color:#1B4D3E;text-decoration:none;font-weight:500;">MoldCo</a>.</p>
    <p style="font-size:12px;color:#BBB;margin:0 0 4px;">
      <a href="https://www.moldco.com/care" style="color:#999;text-decoration:none;">Mold Toxicity Care</a> ·
      <a href="https://www.moldco.com/labs" style="color:#999;text-decoration:none;">Blood Panel Testing</a>
    </p>
    <p style="font-size:11px;color:#CCC;margin:12px 0 0;">Every article AI-curated and compliance-checked. Not medical advice.</p>
  </div>

</div>

</body>
</html>'''

    return html


def main():
    parser = argparse.ArgumentParser(description="The Mold Report — Weekly Newsletter Generator")
    parser.add_argument("--days", type=int, default=7, help="Look back N days (default: 7)")
    parser.add_argument("--preview", action="store_true", help="Print to stdout instead of saving")
    args = parser.parse_args()

    html = generate_newsletter(days=args.days)
    if not html:
        return

    if args.preview:
        print(html)
    else:
        with open(OUTPUT_FILE, "w") as f:
            f.write(html)
        print(f"✅ Newsletter saved to {OUTPUT_FILE.name}")
        print(f"   Open it in a browser to preview, then paste into Substack.")


if __name__ == "__main__":
    main()
