#!/usr/bin/env python3
"""
The Mold Report — Weekly Newsletter Generator (v2)
==================================================
Generates a Substack-ready newsletter from this week's published articles.

Editorial structure (in order):
  1. Header (title + date range)
  2. From the Editors — short, written-by-a-human-sounding intro
  3. The Lead — the most important story this week (research preferred)
  4. The Research Corner — appears whenever research-tagged stories shipped
  5. News & Regulation — what hit the cycle
  6. Industry Pulse — markets, standards, conferences
  7. Quick Hits — everything else worth a click
  8. Footer — MoldCo CTAs (UTMs included; utm_medium=email)

The intro is written by Claude when ANTHROPIC_API_KEY is present (.env loaded
automatically). A strong template fallback runs when the API isn't reachable,
so the script never silently produces something dry.

Substack copy-paste: open newsletter.html in any browser, Cmd+A, Cmd+C, paste.

Usage:
  python generate_newsletter.py              # This week's newsletter
  python generate_newsletter.py --days 14    # Last 14 days
  python generate_newsletter.py --preview    # Print to stdout instead of file
  python generate_newsletter.py --no-ai      # Skip Claude, use template intro
"""

import argparse
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
ARTICLES_FILE = SCRIPT_DIR / "articles.json"
OUTPUT_FILE = SCRIPT_DIR / "newsletter.html"
SITE_URL = "https://themoldreport.org"

# UTM-enabled MoldCo links (every link includes utm_medium=email)
MOLDCO_HOME = "https://www.moldco.com?utm_source=themoldreport&utm_medium=email&utm_campaign=newsletter"
MOLDCO_CARE = "https://www.moldco.com/care?utm_source=themoldreport&utm_medium=email&utm_campaign=newsletter_care"
MOLDCO_PANEL = "https://www.moldco.com/products?utm_source=themoldreport&utm_medium=email&utm_campaign=newsletter_panel"

CATEGORY_LABELS = {
    "research": "Research",
    "regulation": "Regulation",
    "news": "News",
    "industry": "Industry",
    "diagnostics": "Diagnostics",
}


# ---------- Env loading ----------

def load_env():
    """Load .env so ANTHROPIC_API_KEY is picked up without extra deps."""
    env_path = SCRIPT_DIR / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


# ---------- Article loading ----------

def load_articles():
    if ARTICLES_FILE.exists():
        with open(ARTICLES_FILE) as f:
            return json.load(f)
    return {"articles": []}


def get_week_articles(data, days=7):
    """Published articles from the last N days, newest first."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    out = []
    for a in data.get("articles", []):
        if a.get("status") != "published":
            continue
        try:
            pub = datetime.fromisoformat(a["publishedAt"].replace("Z", "+00:00"))
            if pub >= cutoff:
                out.append(a)
        except (ValueError, KeyError):
            continue
    out.sort(key=lambda a: a.get("publishedAt", ""), reverse=True)
    return out


# ---------- Lead story selection ----------

JOURNAL_KEYWORDS = (
    "frontiers", "nature", "lancet", "jama", "nejm", "bmj",
    "cell", "science", "pnas", "plos", "elsevier", "mdpi",
)


def score_lead(a):
    """Higher score = better lead. Research wins by default."""
    score = 0
    cat = a.get("category", "news")
    if cat == "research":
        score += 30
    if a.get("featured"):
        score += 8

    text = " ".join([
        a.get("summary") or "",
        a.get("source") or "",
        " ".join(a.get("tags", []) or []),
    ]).lower()

    if any(j in text for j in JOURNAL_KEYWORDS):
        score += 12
    if "first peer-reviewed" in text or "first direct evidence" in text:
        score += 8
    if "peer-reviewed" in text:
        score += 4
    if "new study" in text or "new research" in text:
        score += 3
    if "shoemaker" in text:
        score += 3
    if "validates" in text or "confirms" in text:
        score += 2
    return score


def pick_lead(articles):
    if not articles:
        return None
    return max(articles, key=score_lead)


def by_category(articles, exclude_ids=None):
    exclude = exclude_ids or set()
    out = {k: [] for k in CATEGORY_LABELS}
    for a in articles:
        if a["id"] in exclude:
            continue
        cat = a.get("category", "news")
        if cat not in out:
            cat = "news"
        out[cat].append(a)
    return out


# ---------- Editorial intro ----------

EDITOR_PROMPT = """You are the editor of The Mold Report, an AI-curated newsroom focused on mold and indoor health. Write the "From the Editors" intro for this week's newsletter ({date_range}).

VOICE — match the tone of The Mold Report's About page:
- Smart, clear, journalist-sounding. Slightly cheeky but never flippant.
- Plain English. No jargon stacks. No corporate hedging.
- Treats the illness with respect; treats hype with skepticism.
- Owns the AI-newsroom angle when it fits naturally — but doesn't lean on it every week.

CONSTRAINTS:
- Two short paragraphs, 90-140 words total.
- First paragraph: spotlight THIS WEEK'S LEAD STORY. Why does it matter? Frame it for a smart reader who isn't a researcher.
- Second paragraph: pull the camera back. Tease one or two other threads from the week (lawsuits, regulators, market signals, individual cases) without listing them.
- End with a single sentence that invites the reader in. Concise. Confident.
- Do NOT use bullet points or sub-headers.
- Do NOT make medical claims. Do NOT promise outcomes.
- Avoid: "groundbreaking," "game-changing," "revolutionary," "in today's edition," "we are excited to."
- Refer to the publication as "we" or "The Mold Report," never as "I."

Context for the week:
{context}

Output ONLY two <p> tags with the intro. No surrounding text, no quotes, no commentary."""


def _build_intro_context(lead, sections, total):
    research = sections.get("research", [])
    news = sections.get("news", [])
    regulation = sections.get("regulation", [])
    industry = sections.get("industry", [])

    other_lines = []
    for cat, items in [("research", research), ("regulation", regulation), ("news", news), ("industry", industry)]:
        for a in items[:3]:
            other_lines.append(f"- [{cat}] {a['title']} (source: {a.get('source','')})")

    return (
        f"Lead story: {lead['title']}\n"
        f"Lead category: {lead.get('category')}\n"
        f"Lead source: {lead.get('source')}\n"
        f"Lead summary: {(lead.get('summary') or '')[:600]}\n"
        f"Lead editor's note: {(lead.get('editorsNote') or '')[:400]}\n\n"
        f"Counts this week: research={len(research)}, news={len(news)}, "
        f"regulation={len(regulation)}, industry={len(industry)}, total={total}\n\n"
        f"Other notable stories:\n" + ("\n".join(other_lines) or "(none)")
    )


def ai_intro(lead, sections, total, date_range):
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return None
    try:
        from anthropic import Anthropic
    except ImportError:
        return None

    prompt = EDITOR_PROMPT.format(
        date_range=date_range,
        context=_build_intro_context(lead, sections, total),
    )

    # Try a couple of models in fallback order, in case one isn't enabled.
    candidates = [
        "claude-sonnet-4-5",
        "claude-haiku-4-5-20251001",
        "claude-3-5-sonnet-latest",
    ]
    for model in candidates:
        try:
            client = Anthropic(api_key=api_key)
            msg = client.messages.create(
                model=model,
                max_tokens=600,
                messages=[{"role": "user", "content": prompt}],
            )
            text = msg.content[0].text.strip()
            # Clean stray code fences if the model gets cute
            if text.startswith("```"):
                text = text.strip("`").lstrip("html").strip()
            if "<p" in text:
                return text
        except Exception:
            continue
    return None


def template_intro(lead, sections, total, date_range):
    """Hand-crafted fallback that adapts to the lead category."""
    research = sections.get("research", [])
    regulation = sections.get("regulation", [])
    industry = sections.get("industry", [])
    news = sections.get("news", [])

    cat = lead.get("category", "news")
    title = lead["title"]

    if cat == "research":
        p1 = (
            f"<p>The biggest headline this week didn't come from a courtroom or a school board "
            f"meeting — it came from a journal. <em>{title}</em>. Translation for everyone who "
            f"isn't waist-deep in the literature: a piece of the Shoemaker biotoxin pathway that "
            f"has been hypothesized for years now has direct molecular evidence behind it. That "
            f"doesn't change tomorrow's appointment, but it does change how the conversation gets had.</p>"
        )
    elif cat == "regulation":
        p1 = (
            f"<p>The headline that stuck this week is a regulatory one: <em>{title}</em>. Mold "
            f"cases in courts and agencies aren't new, but the rulings keep getting more specific "
            f"— and more expensive — and that is a slow signal worth tracking.</p>"
        )
    elif cat == "industry":
        p1 = (
            f"<p>The story we kept circling back to this week sits on the business side: "
            f"<em>{title}</em>. The remediation and testing markets are quietly turning into "
            f"something the broader healthcare conversation is going to have to reckon with.</p>"
        )
    else:
        p1 = (
            f"<p>The story that wouldn't leave us alone this week: <em>{title}</em>. We're "
            f"including it not for the spectacle but for the pattern — same symptoms, same delay "
            f"to diagnosis, same eventual return to the home environment as the missing piece.</p>"
        )

    threads = []
    research_left = len(research) - (1 if cat == "research" else 0)
    if research_left > 0:
        threads.append("more peer-reviewed work landing on mold and chronic inflammation")
    if regulation and cat != "regulation":
        threads.append("courts and inspectors stacking up another set of rulings")
    if industry and cat != "industry":
        threads.append("the remediation and testing markets continuing their march")
    if news and cat != "news":
        threads.append("personal stories finding their way into mainstream coverage")
    if not threads:
        threads.append("a steady stream of stories worth your attention")

    teaser = "; ".join(threads[:3])
    p2 = (
        f"<p>Underneath the lead: {teaser}. {total} stories cleared the editors this week. "
        f"The ones that survived are below.</p>"
    )

    return p1 + "\n" + p2


# ---------- Rendering ----------

def truncate(text, length=200):
    if len(text) <= length:
        return text
    for end in [". ", "? ", "! "]:
        idx = text[:length].rfind(end)
        if idx > length * 0.5:
            return text[: idx + 1]
    idx = text[:length].rfind(" ")
    return (text[:idx] + "…") if idx > 0 else (text[:length] + "…")


def article_url(a):
    return f"{SITE_URL}/a/{a['id']}.html"


def render_lead(a):
    cat = CATEGORY_LABELS.get(a.get("category", "news"), "News")
    source = a.get("source", "")
    summary = truncate(a.get("summary", ""), 320)
    out = [
        '<hr>',
        '<h2>The Lead</h2>',
        f'<h3><a href="{article_url(a)}">{a["title"]}</a></h3>',
        f'<p><strong>{cat}</strong> · {source}</p>',
        f'<p>{summary}</p>',
    ]
    if a.get("editorsNote"):
        out.append(
            f"<blockquote><strong>Editor's Note:</strong> {a['editorsNote']}</blockquote>"
        )
    out.append(f'<p><a href="{article_url(a)}">Read the full story →</a></p>')
    return "\n".join(out)


def render_research_corner(items):
    if not items:
        return ""
    out = [
        '<hr>',
        '<h2>The Research Corner</h2>',
        '<p><em>Studies, papers, and clinical work that landed this week.</em></p>',
    ]
    for a in items[:4]:
        cat = CATEGORY_LABELS.get(a.get("category", "research"), "Research")
        source = a.get("source", "")
        summary = truncate(a.get("summary", ""), 220)
        out.append(f'<h3><a href="{article_url(a)}">{a["title"]}</a></h3>')
        out.append(f'<p><strong>{cat}</strong> · {source}</p>')
        out.append(f'<p>{summary}</p>')
        if a.get("editorsNote"):
            out.append(
                f"<blockquote><strong>Why it matters:</strong> "
                f"{truncate(a['editorsNote'], 240)}</blockquote>"
            )
        out.append(f'<p><a href="{article_url(a)}">Read the full story →</a></p>')
    return "\n".join(out)


def render_section(title, intro, items, n=3):
    if not items:
        return ""
    out = ['<hr>', f'<h2>{title}</h2>']
    if intro:
        out.append(f'<p><em>{intro}</em></p>')
    for a in items[:n]:
        cat = CATEGORY_LABELS.get(a.get("category", "news"), "News")
        source = a.get("source", "")
        summary = truncate(a.get("summary", ""), 200)
        out.append(f'<h3><a href="{article_url(a)}">{a["title"]}</a></h3>')
        out.append(f'<p><strong>{cat}</strong> · {source}</p>')
        out.append(f'<p>{summary}</p>')
        out.append(f'<p><a href="{article_url(a)}">Read →</a></p>')
    return "\n".join(out)


def render_quick_hits(items):
    if not items:
        return ""
    out = [
        '<hr>',
        '<h2>Quick Hits</h2>',
        '<p><em>Worth a click, not a deep-dive.</em></p>',
        '<ul>',
    ]
    for a in items:
        cat = CATEGORY_LABELS.get(a.get("category", "news"), "News")
        out.append(
            f'<li><a href="{article_url(a)}"><strong>{a["title"]}</strong></a> · {cat}</li>'
        )
    out.append('</ul>')
    return "\n".join(out)


def render_footer():
    return "\n".join([
        '<hr>',
        f'<p>Read every story on the site — no paywall, no login: '
        f'<a href="{SITE_URL}"><strong>themoldreport.org</strong></a>.</p>',
        '<hr>',
        f'<p><em>The Mold Report is published by the team behind '
        f'<a href="{MOLDCO_HOME}">MoldCo</a>, a clinician-led virtual clinic focused on mold toxicity.</em></p>',
        f'<p><a href="{MOLDCO_CARE}">Mold Toxicity Care</a> · '
        f'<a href="{MOLDCO_PANEL}">Blood Panel Testing</a></p>',
        '<p><em>Every article AI-curated and compliance-checked. Not medical advice.</em></p>',
    ])


def format_date_range(days=7):
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    if start.month == end.month:
        return f"{start.strftime('%B %d')}–{end.strftime('%d, %Y')}"
    return f"{start.strftime('%B %d')} – {end.strftime('%B %d, %Y')}"


# ---------- Main ----------

def generate_newsletter(days=7, no_ai=False):
    data = load_articles()
    articles = get_week_articles(data, days=days)
    if not articles:
        print(f"No articles published in the last {days} days. Nothing to send.")
        return None

    date_range = format_date_range(days)
    total = len(articles)

    lead = pick_lead(articles)
    sections_all = by_category(articles)
    sections_excl_lead = by_category(articles, exclude_ids={lead["id"]})

    # Editorial intro: AI first, template fallback
    intro_html = None if no_ai else ai_intro(lead, sections_excl_lead, total, date_range)
    intro_source = "ai"
    if not intro_html:
        intro_html = template_intro(lead, sections_excl_lead, total, date_range)
        intro_source = "template"

    used = {lead["id"]}

    research_items = [a for a in sections_all.get("research", []) if a["id"] not in used]
    research_html = render_research_corner(research_items[:4])
    used |= {a["id"] for a in research_items[:4]}

    news_items = [
        a for a in (sections_all.get("news", []) + sections_all.get("regulation", []))
        if a["id"] not in used
    ]
    news_items.sort(key=lambda x: x.get("publishedAt", ""), reverse=True)
    news_html = render_section(
        "News & Regulation",
        "What hit the cycle: lawsuits, schools, housing, and the human stories.",
        news_items,
        n=4,
    )
    used |= {a["id"] for a in news_items[:4]}

    industry_items = [a for a in sections_all.get("industry", []) if a["id"] not in used]
    industry_html = render_section(
        "Industry Pulse",
        "Markets, standards, conferences.",
        industry_items,
        n=3,
    )
    used |= {a["id"] for a in industry_items[:3]}

    leftover = [a for a in articles if a["id"] not in used]
    quick_html = render_quick_hits(leftover)

    parts = [
        '<h1>The Mold Report</h1>',
        f'<p><em>Weekly · {date_range}</em></p>',
        '<hr>',
        '<h2>From the Editors</h2>',
        intro_html,
        render_lead(lead),
    ]
    if research_html:
        parts.append(research_html)
    if news_html:
        parts.append(news_html)
    if industry_html:
        parts.append(industry_html)
    if quick_html:
        parts.append(quick_html)
    parts.append(render_footer())

    return "\n\n".join(p for p in parts if p), intro_source


def main():
    parser = argparse.ArgumentParser(
        description="The Mold Report — Weekly Newsletter Generator (v2)"
    )
    parser.add_argument("--days", type=int, default=7, help="Look back N days (default: 7)")
    parser.add_argument("--preview", action="store_true", help="Print to stdout instead of saving")
    parser.add_argument("--no-ai", action="store_true", help="Skip Claude, use template intro")
    args = parser.parse_args()

    load_env()
    result = generate_newsletter(days=args.days, no_ai=args.no_ai)
    if not result:
        return
    html, intro_source = result

    if args.preview:
        print(html)
        return

    with open(OUTPUT_FILE, "w") as f:
        f.write(html)

    print(f"✅ Newsletter saved to {OUTPUT_FILE.name} (intro: {intro_source})")
    print()
    print("→ Open it in any browser, Cmd+A, Cmd+C, paste into Substack's editor.")
    print("  Substack handles the formatting cleanly. No edits needed.")


if __name__ == "__main__":
    main()
