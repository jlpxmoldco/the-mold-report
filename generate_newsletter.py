#!/usr/bin/env python3
"""
The Mold Report — Weekly Newsletter Generator (v3)
==================================================
Generates a Substack-ready newsletter from this week's published articles.

The host of the newsletter is MARLOW — the AI newsletter editor with a name,
a voice, and a long-running argument with the other bots downstairs (The
Critic, The Lawyer, The Scientist — all already named on the About page).
Marlow opens every issue, signs off at the bottom, and keeps the tone fun
throughout. The newsletter is unapologetically AI-curated; we lean into it.

Editorial structure (in order):
  1. Header (title + date range)
  2. From Marlow — warm, conversational editor's letter
  3. The Lead — most important story, research preferred
  4. The Research Corner — appears whenever research-tagged stories shipped
  5. News & Regulation — what hit the cycle
  6. Industry Pulse — markets, standards, conferences
  7. Quick Hits — everything else worth a click
  8. Sign-off — Marlow wraps with a P.S. running gag
  9. Footer — MoldCo CTAs (UTMs included; utm_medium=email)

Editor's notes are short by design now: one sentence on the lead, one
sentence in the Research Corner. News/regulation/industry stand on their
summaries alone — no medical mini-lectures.

The intro is written by Claude when ANTHROPIC_API_KEY is reachable. A
strong template fallback runs otherwise, also in Marlow's voice, so the
newsletter never reads dry.

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
import random
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

EDITOR_NAME = "Marlow"


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


# ---------- Editorial intro (Marlow's voice) ----------

EDITOR_PROMPT = """You are MARLOW, the AI editor of The Mold Report — a newsroom focused on mold and indoor health. Write the editor's letter at the top of this week's newsletter ({date_range}).

WHO MARLOW IS — a recurring character readers should grow attached to:
- An AI newsletter editor. Self-aware about being an AI and embraces it without being weird about it.
- Sharp, dryly funny, mildly cheeky. Reads like a smart human friend who happens to read every mold paper that hits the wires.
- Cares about people with mold illness. Treats the illness with respect; treats hype, charlatans, and overreach with skepticism.
- Has a long-running, affectionate argument with the other bots in the newsroom (already named on the About page):
    - The Critic (interest scoring, hardliner)
    - The Hook (headline rewriter, opinionated about semicolons)
    - The Writer (rewrites every summary)
    - The Lawyer (compliance, obsessed with the difference between "treatment" and "recovery")
    - The Scientist (verifies research against the evidence base)
    - The Optimizer (SEO, fights about meta descriptions)
- Marlow can casually reference these bots by name for color, but only if it lands naturally — never forced.

CRITICAL — INTRODUCE MARLOW EVERY WEEK:
The first non-greeting paragraph MUST explicitly introduce Marlow as the AI editor behind this newsletter. Readers may be opening their first issue. They should never have to guess who Marlow is. Examples of acceptable phrasings (vary across issues, don't reuse the same one):
- "Marlow here — your AI editor at The Mold Report."
- "Marlow here. I'm the AI editor who runs this newsletter."
- "Marlow here, the AI putting this newsletter together every week."
- "Marlow here — AI editor in residence at The Mold Report."

VOICE:
- First person ("I", "me", or "we" for the newsroom collectively).
- Conversational, like a Substack you actually want to open. Plain English. No jargon stacks. No corporate hedging.
- Lean into specifics from the week with color and proper nouns. If something is absurd (a Disney ride, a tent, a $13K landlord ruling), name it.
- Allowed: light wit, parentheticals, the occasional aside about the bots.
- Avoid: "groundbreaking," "game-changing," "revolutionary," "in today's edition," "we are excited to," "buckle up."

STRUCTURE — output exactly this shape, no more, no less:
1. <p>Hey friends,</p>  (or a varied warm greeting — "Friends —", "Hey readers,", "Hi all —")
2. <p>Opening paragraph that explicitly introduces Marlow as the AI editor behind this newsletter and sets up the week. ~30-55 words.</p>
3. <p>Lead-story paragraph — frame this week's lead in plain English with personality. ~50-80 words.</p>
4. <p>"Also this week" paragraph — name 2-3 OTHER specific stories with color, in prose, not a list. ~50-80 words.</p>
5. <p>One short closer line. Examples: "Let's dig in.", "Onward.", "Pour the coffee.", "Here we go." — vary it.</p>

CONSTRAINTS:
- Total word count across all paragraphs: 160-230 words.
- No bullet points, no sub-headers, no markdown.
- No medical claims. No promised outcomes.
- Do NOT repeat the lead headline verbatim — paraphrase or describe.
- Output ONLY the five <p> tags above, nothing else. No code fences, no commentary.

CONTEXT FOR THIS WEEK:
{context}"""


SIGNOFF_PSS = [
    "<em>P.S. The Lawyer reminds me this isn't medical advice. The Lawyer reminds everyone, of everything, constantly.</em>",
    "<em>P.S. The Scientist asked me to clarify that one study moves the dial — it doesn't end the conversation. Noted, Scientist. Noted.</em>",
    "<em>P.S. The Critic says we should have killed two more stories. The Critic always says that.</em>",
    "<em>P.S. The Hook fought me on three headlines this week. The Hook won twice.</em>",
    "<em>P.S. None of this is medical advice. The Lawyer drafted that sentence and I'm contractually required to print it.</em>",
]


def _build_intro_context(lead, sections, total):
    research = sections.get("research", [])
    news = sections.get("news", [])
    regulation = sections.get("regulation", [])
    industry = sections.get("industry", [])

    other_lines = []
    for cat, items in [("research", research), ("regulation", regulation), ("news", news), ("industry", industry)]:
        for a in items[:5]:
            other_lines.append(f"- [{cat}] {a['title']} (source: {a.get('source','')})")

    return (
        f"Lead story: {lead['title']}\n"
        f"Lead category: {lead.get('category')}\n"
        f"Lead source: {lead.get('source')}\n"
        f"Lead summary: {(lead.get('summary') or '')[:600]}\n\n"
        f"Counts this week: research={len(research)}, news={len(news)}, "
        f"regulation={len(regulation)}, industry={len(industry)}, total={total}\n\n"
        f"Other notable stories (use specifics from these for color):\n"
        + ("\n".join(other_lines) or "(none)")
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
                max_tokens=900,
                messages=[{"role": "user", "content": prompt}],
            )
            text = msg.content[0].text.strip()
            if text.startswith("```"):
                text = text.strip("`").lstrip("html").strip()
            if "<p" in text:
                return text
        except Exception:
            continue
    return None


# ---- Template fallback (still in Marlow's voice) ----

# Map common story patterns to short, voiced references for the "also this week" line.
def humanize_thread(a):
    title = a["title"]
    text = (title + " " + (a.get("summary") or "")).lower()

    if "disney" in text and "small world" in text:
        return "Disney pulled black mold out of <em>It's a Small World</em> (yes, that ride)"
    if "auckland" in text and "landlord" in text:
        return "an Auckland landlord owes a tenant NZ$13,000"
    if "spring hill college" in text:
        return "Spring Hill College is fielding an alumna's lawsuit"
    if "50-pound" in text or "50 pound" in text or "50 lb" in text or "50-lb" in text or "50 lbs" in text:
        return "a health influencer's husband dropped 50 pounds before they checked the walls"
    if "tent" in text and "landlord" in text:
        return "a Georgia tenant ended up living in a tent because the landlord didn't act"
    if "ndaa" in text or "military housing" in text:
        return "the 2026 NDAA name-checked remediation standards for military housing"
    if "dorm" in text or ("asu" in text and "students" in text):
        return "ASU students are still dealing with dorm mold months later"
    if "wisconsin" in text and "flood" in text:
        return "a Wisconsin doctor is reminding flood victims that mold growth starts in days, not weeks"
    if "cirsx" in text or ("conference" in text and "fort lauderdale" in text):
        return "CIRSx booked Fort Lauderdale for June"
    if "remediation market" in text or "$4 billion" in text or "$3.9 billion" in text:
        return "the remediation market is closing in on $4 billion"
    if "mycotoxin testing" in text or "$2.78 billion" in text:
        return "the mycotoxin testing market is on track to nearly double by 2034"
    if "reality star" in text or "people magazine" in text:
        return "a reality star's CIRS diagnosis got mainstream coverage"
    if "fire department" in text or "fire dept" in text:
        return "a fire department got cited for mold and code violations (firefighters, of all people)"
    if "quaker farms" in text or ("school" in text and "parents" in text):
        return "school parents are demanding faster, clearer answers about mold remediation"
    if "depression" in text and "research" in text:
        return "mainstream depression coverage is starting to mention environmental factors"
    if "lung function" in text:
        return "a long-term study connected childhood mold exposure to reduced lung function"
    if "invasive mold" in text or "mucormycosis" in text or "aspergillosis" in text:
        return "ID specialists are circling the harder end of the spectrum — invasive infections"
    # Fallback: short title in italics
    short = title if len(title) < 90 else title[:87] + "…"
    return f"<em>{short}</em>"


def _pick_other_threads(articles, lead, n=3):
    """Pick the most newsletter-worthy non-lead stories."""
    candidates = [a for a in articles if a["id"] != lead["id"]]

    def score(a):
        s = 0
        text = (a["title"] + " " + (a.get("summary") or "")).lower()
        # Specific cult-favorite signals
        if "disney" in text and "small world" in text:
            s += 25
        if "tent" in text and "landlord" in text:
            s += 18
        if "auckland" in text and "$" in text:
            s += 14
        if "50-pound" in text or "50 pound" in text or "50 lb" in text:
            s += 14
        if "ndaa" in text:
            s += 10
        if "spring hill" in text:
            s += 9
        if "lawsuit" in text or "ordered to pay" in text:
            s += 6
        if "$" in a["title"] or "billion" in a["title"].lower():
            s += 5
        if a.get("category") == "research":
            s += 3
        if "conference" in text:
            s += 2
        return s

    candidates.sort(key=score, reverse=True)
    return candidates[:n]


def template_intro(lead, articles, sections, total, date_range):
    """Hand-crafted Marlow-voiced fallback when the API isn't reachable."""
    lead_title = lead["title"]
    lead_cat = lead.get("category", "news")
    lead_source = lead.get("source", "")

    week_num = datetime.now().isocalendar()[1]
    greetings = ["Hey friends,", "Friends —", "Hey readers,", "Hi all —"]
    greeting = greetings[week_num % len(greetings)]

    # Each opener MUST explicitly introduce Marlow as the AI editor behind
    # this newsletter — readers may be on their first issue.
    openers = [
        f"Marlow here — I'm the AI editor behind The Mold Report, the one who reads every mold paper and lawsuit so you don't have to. {total} stories cleared the bots this week. Here's the rundown.",
        f"Marlow here. I'm the AI editor who runs this newsletter — every Sunday I take what the bots downstairs scrape, score, and rewrite, and serve up the keepers. We've got {total} of them this week.",
        f"Marlow here, your AI editor at The Mold Report. {total} stories made it through editorial this week — some big, some absurd, all of them below.",
        f"Marlow here — AI editor in residence at The Mold Report, fresh off another week of scoring fights with The Critic. {total} stories made the cut. We'll start with the one that surprised the room.",
    ]
    opener = openers[week_num % len(openers)]

    if lead_cat == "research":
        p_lead = (
            f"<p>Mold news doesn't usually open with peer review, but this week did. <em>{lead_source}</em> "
            f"published the first direct molecular evidence behind a step of the Shoemaker biotoxin "
            f"pathway — the part where MARCoNS bacteria suppress alpha-MSH. Translation for everyone "
            f"who isn't waist-deep in the literature: a mechanism that's lived in the protocol for "
            f"years now has receipts. The Scientist nodded at a screen. That's a big deal in this house.</p>"
        )
    elif lead_cat == "regulation":
        p_lead = (
            f"<p>The loudest headline this week came from the regulatory side: <em>{lead_title}</em>. "
            f"Mold cases in courts and agencies aren't new, but the rulings keep getting more specific, "
            f"more expensive, and harder to wave off. The Lawyer is taking notes.</p>"
        )
    elif lead_cat == "industry":
        p_lead = (
            f"<p>Lead story this week sits on the business side: <em>{lead_title}</em>. The remediation "
            f"and testing markets are quietly turning into something the broader healthcare conversation "
            f"is going to have to reckon with.</p>"
        )
    else:
        p_lead = (
            f"<p>The story we kept circling back to: <em>{lead_title}</em>. Same pattern we keep "
            f"watching — symptoms that don't fit the standard workup, then someone finally checks the walls.</p>"
        )

    other_threads = _pick_other_threads(articles, lead, n=3)
    phrases = [humanize_thread(a) for a in other_threads]
    phrases = [p for p in phrases if p]
    if phrases:
        joined = "; ".join(phrases)
        # Capitalize the first letter of the joined string for a clean sentence start
        if joined and joined[0].islower():
            joined = joined[0].upper() + joined[1:]
        p_also = f"<p>Also on the list this week: {joined}. The patterns are the patterns.</p>"
    else:
        p_also = "<p>The rest of the week is below.</p>"

    closers = ["<p>Let's dig in.</p>", "<p>Onward.</p>", "<p>Pour the coffee.</p>", "<p>Here we go.</p>"]
    closer = closers[week_num % len(closers)]

    return (
        f"<p>{greeting}</p>\n"
        f"<p>{opener}</p>\n"
        f"{p_lead}\n"
        f"{p_also}\n"
        f"{closer}"
    )


# ---------- Rendering ----------

_ABBREVS = {
    "dr.", "mr.", "mrs.", "ms.", "st.", "jr.", "sr.", "vs.",
    "etc.", "e.g.", "i.e.", "ph.d.", "u.s.", "u.k.",
}


def first_sentence(text, cap=280):
    """Return the first complete sentence of `text`, capped at `cap` chars.

    Handles common abbreviations ('Dr.', 'U.S.', 'e.g.') so we don't slice
    the editor's note in half mid-name.
    """
    if not text:
        return ""
    text = text.strip()
    L = len(text)
    i = 0
    while i < L:
        ch = text[i]
        if ch in ".!?" and (i + 1 >= L or text[i + 1] in (" ", "\n")):
            # Look back at the trailing token to see if it's an abbreviation.
            start = i
            while start > 0 and text[start - 1] not in (" ", "\n"):
                start -= 1
            token = text[start: i + 1].lower().lstrip("([{\"'`")
            if token not in _ABBREVS:
                sent = text[: i + 1].strip()
                if len(sent) <= cap:
                    return sent
                cut = sent.rfind(" ", 0, cap)
                return (sent[:cut] + "…") if cut > 0 else sent[:cap] + "…"
        i += 1
    if len(text) > cap:
        cut = text.rfind(" ", 0, cap)
        return (text[:cut] + "…") if cut > 0 else text[:cap] + "…"
    return text


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
    summary = truncate(a.get("summary", ""), 300)
    out = [
        '<hr>',
        '<h2>The Lead</h2>',
        f'<h3><a href="{article_url(a)}">{a["title"]}</a></h3>',
        f'<p><strong>{cat}</strong> · {source}</p>',
        f'<p>{summary}</p>',
    ]
    note = first_sentence(a.get("editorsNote") or "", cap=300)
    if note:
        out.append(f'<blockquote><strong>Why it matters:</strong> {note}</blockquote>')
    out.append(f'<p><a href="{article_url(a)}">Read the full story →</a></p>')
    return "\n".join(out)


def render_research_corner(items):
    if not items:
        return ""
    out = [
        '<hr>',
        '<h2>The Research Corner</h2>',
        '<p><em>Studies, papers, and clinical work that landed this week. The Scientist insisted on this section.</em></p>',
    ]
    for a in items[:4]:
        cat = CATEGORY_LABELS.get(a.get("category", "research"), "Research")
        source = a.get("source", "")
        summary = truncate(a.get("summary", ""), 200)
        out.append(f'<h3><a href="{article_url(a)}">{a["title"]}</a></h3>')
        out.append(f'<p><strong>{cat}</strong> · {source}</p>')
        out.append(f'<p>{summary}</p>')
        note = first_sentence(a.get("editorsNote") or "", cap=260)
        if note:
            out.append(f'<blockquote><strong>The takeaway:</strong> {note}</blockquote>')
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
        summary = truncate(a.get("summary", ""), 180)
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
        '<p><em>One click each. The Critic gave these all 7s.</em></p>',
        '<ul>',
    ]
    for a in items:
        cat = CATEGORY_LABELS.get(a.get("category", "news"), "News")
        out.append(
            f'<li><a href="{article_url(a)}"><strong>{a["title"]}</strong></a> · {cat}</li>'
        )
    out.append('</ul>')
    return "\n".join(out)


def render_signoff():
    week_num = datetime.now().isocalendar()[1]
    closers = [
        "That's the week.",
        "That's it from the bots.",
        "That's the inbox emptied.",
        "That's the weekly download.",
    ]
    closer = closers[week_num % len(closers)]
    ps = SIGNOFF_PSS[week_num % len(SIGNOFF_PSS)]
    return "\n".join([
        '<hr>',
        f'<p>{closer} Read every story on the site — no paywall, no login: '
        f'<a href="{SITE_URL}"><strong>themoldreport.org</strong></a>.</p>',
        f'<p>— {EDITOR_NAME}</p>',
        f'<p>{ps}</p>',
    ])


def render_footer():
    return "\n".join([
        '<hr>',
        f'<p><em>The Mold Report is published by the team behind '
        f'<a href="{MOLDCO_HOME}">MoldCo</a>, a clinician-led virtual clinic focused on mold toxicity. '
        f'Marlow is an AI; the science is real.</em></p>',
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

    intro_html = None if no_ai else ai_intro(lead, sections_excl_lead, total, date_range)
    intro_source = "ai"
    if not intro_html:
        intro_html = template_intro(lead, articles, sections_excl_lead, total, date_range)
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
        "Markets, standards, conferences. The Optimizer pretends not to care.",
        industry_items,
        n=3,
    )
    used |= {a["id"] for a in industry_items[:3]}

    leftover = [a for a in articles if a["id"] not in used]
    quick_html = render_quick_hits(leftover)

    parts = [
        '<h1>The Mold Report</h1>',
        f'<p><em>Weekly · {date_range} · with {EDITOR_NAME}, your AI editor</em></p>',
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
    parts.append(render_signoff())
    parts.append(render_footer())

    return "\n\n".join(p for p in parts if p), intro_source


def main():
    parser = argparse.ArgumentParser(
        description="The Mold Report — Weekly Newsletter Generator (v3)"
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
