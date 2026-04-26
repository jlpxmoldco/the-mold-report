#!/usr/bin/env python3
"""
The Mold Report — Weekly Newsletter Generator
================================================
Generates a Substack-ready newsletter from this week's published articles.
No API calls. Zero cost. Just formats what's already in articles.json.

Substack strips most inline CSS. This generator outputs SIMPLE HTML that
Substack's rich-text editor handles cleanly: headings, paragraphs, bold,
links, horizontal rules, and basic lists. Nothing fancy.

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
SITE_URL = "https://themoldreport.org"


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
    featured = [a for a in articles if a.get("featured")]
    rest = [a for a in articles if not a.get("featured")]
    return (featured + rest)[:n]


def pick_remaining(articles, already_used_ids, n=7):
    """Pick remaining articles for the roundup list."""
    return [a for a in articles if a["id"] not in already_used_ids][:n]


def format_date_range(days=7):
    """Format the date range for the newsletter header."""
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    if start.month == end.month:
        return f"{start.strftime('%B %d')} – {end.strftime('%d, %Y')}"
    return f"{start.strftime('%B %d')} – {end.strftime('%B %d, %Y')}"


def truncate(text, length=160):
    """Truncate text cleanly at a sentence or word boundary."""
    if len(text) <= length:
        return text
    for end in [". ", "? ", "! "]:
        idx = text[:length].rfind(end)
        if idx > length * 0.5:
            return text[:idx + 1]
    idx = text[:length].rfind(" ")
    return text[:idx] + "…" if idx > 0 else text[:length] + "…"


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
    """Generate Substack-friendly newsletter HTML."""
    data = load_articles()
    articles = get_week_articles(data, days=days)

    if not articles:
        print(f"No articles published in the last {days} days. Nothing to send.")
        return None

    date_range = format_date_range(days)
    total_count = len(articles)

    # Pick sections
    top_stories = pick_top_stories(articles, n=3)
    used_ids = {a["id"] for a in top_stories}
    remaining = pick_remaining(articles, used_ids, n=7)

    # Rotating intro
    week_num = datetime.now().isocalendar()[1]
    intros = [
        f"Your AI editors processed {total_count} stories this week. Here's what made the cut.",
        f"{total_count} articles came through the pipeline this week. Nine AI editors argued about them. These survived.",
        f"The pipeline reviewed {total_count} stories. Most didn't make it. These did.",
        f"Another week, another {total_count} stories through the gauntlet. Here's what's worth your time.",
    ]
    intro = intros[week_num % len(intros)]

    # --- BUILD SIMPLE HTML ---
    # Substack preserves: h1-h3, p, a, strong, em, hr, ul/ol/li, blockquote
    # Substack strips: div, background, border-radius, most inline styles

    lines = []

    # Header
    lines.append(f'<h1>The Mold Report</h1>')
    lines.append(f'<p><em>Weekly · {date_range}</em></p>')
    lines.append(f'<p>{intro}</p>')
    lines.append('<hr>')

    # Top Stories
    lines.append('<h2>Top Stories</h2>')
    for a in top_stories:
        link = f"{SITE_URL}/a/{a['id']}.html"
        summary = truncate(a["summary"], 180)
        source = a.get("source", "")
        cat = category_label(a.get("category", "news"))

        lines.append(f'<h3><a href="{link}">{a["title"]}</a></h3>')
        lines.append(f'<p><strong>{cat}</strong> · {source}</p>')
        lines.append(f'<p>{summary}</p>')

        if a.get("editorsNote"):
            lines.append(f'<blockquote><strong>Editor\'s Note:</strong> {a["editorsNote"]}</blockquote>')

        lines.append(f'<p><a href="{link}">Read full story →</a></p>')
        lines.append('')

    # More Stories (simple list)
    if remaining:
        lines.append('<hr>')
        lines.append('<h2>More This Week</h2>')
        lines.append('<ul>')
        for a in remaining:
            link = f"{SITE_URL}/a/{a['id']}.html"
            cat = category_label(a.get("category", "news"))
            lines.append(f'<li><a href="{link}"><strong>{a["title"]}</strong></a> · {cat}</li>')
        lines.append('</ul>')

    # CTA
    lines.append('<hr>')
    lines.append(f'<p>Read every story on the site — no paywall, no login: <a href="{SITE_URL}"><strong>themoldreport.org</strong></a></p>')

    # Footer
    lines.append('<hr>')
    moldco_link = "https://www.moldco.com?utm_source=themoldreport&utm_medium=email&utm_campaign=newsletter"
    care_link = "https://www.moldco.com/care?utm_source=themoldreport&utm_medium=email&utm_campaign=newsletter_care"
    panel_link = "https://www.moldco.com/products?utm_source=themoldreport&utm_medium=email&utm_campaign=newsletter_panel"
    lines.append(f'<p><em>The Mold Report is published by the team behind <a href="{moldco_link}">MoldCo</a>.</em></p>')
    lines.append(f'<p><a href="{care_link}">Mold Toxicity Care</a> · <a href="{panel_link}">Blood Panel Testing</a></p>')
    lines.append(f'<p><em>Every article AI-curated and compliance-checked. Not medical advice.</em></p>')

    html = '\n'.join(lines)
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
        print(f"   Copy-paste into Substack's editor. It just works.")


if __name__ == "__main__":
    main()
