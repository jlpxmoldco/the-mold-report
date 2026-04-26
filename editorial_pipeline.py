#!/usr/bin/env python3
"""
The Mold Report — Fully Automated AI Editorial Pipeline
=========================================================
Runs hands-off. Fetches mold news from Google Alerts RSS feeds,
processes each article through a 7-gate AI pipeline, and auto-publishes
anything that scores 7+ on editorial interest. No human step required.

Pipeline gates (in order):
  0. DEDUP AGENT        — Content-aware duplicate detection (URL, title, entity overlap)
  1. FRESHNESS GATE     — Rejects articles older than 90 days
  2. SOURCE GATE        — Rejects articles without valid source URLs (tips exempt)
  2b. TIP SCREENING     — Reader tips: checks editorial validity + Shoemaker alignment
  3. INTEREST AGENT     — Scores 1-10 on newsworthiness (must be >= 7)
  4. HEADLINE AGENT     — Rewrites titles for clarity and reader engagement
  5. EDITORIAL AGENT    — Rewrites summary in Mold Report voice
  6. COMPLIANCE AGENT   — Auto-corrects terminology against MoldCo claims rules
  7. RESEARCH AGENT     — Verifies Shoemaker alignment (kills off-topic content)
  8. PHOTO AGENT        — Assigns images (OG → topic Unsplash → category fallback)
  9. SEO AGENT          — Generates search-optimized meta title + description per article

Data storage:
  articles.json — flat JSON file. This IS the database. No server needed.
  index.html reads articles.json (or uses embedded fallback for file:// access).

Usage:
  # Default: full auto-publish pipeline
  python editorial_pipeline.py

  # Dry run (process but don't save)
  python editorial_pipeline.py --dry-run

  # Just compliance-check existing articles
  python editorial_pipeline.py --compliance-check

  # Override interest threshold (default 7)
  python editorial_pipeline.py --min-score 6

Requirements:
  pip install anthropic feedparser requests beautifulsoup4
"""

import json
import os
import hashlib
import argparse
from datetime import datetime, timezone
from pathlib import Path
import re
from urllib.parse import urlparse, parse_qs

# Load .env file if present
_env_file = Path(__file__).parent / ".env"
if _env_file.exists():
    with open(_env_file) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith('#') and '=' in _line:
                _k, _v = _line.split('=', 1)
                _k, _v = _k.strip(), _v.strip()
                # Override empty env vars (some environments pre-set keys to "")
                if _v and (not os.environ.get(_k)):
                    os.environ[_k] = _v

try:
    import anthropic
except ImportError:
    anthropic = None

try:
    import feedparser
except ImportError:
    feedparser = None

try:
    import requests
except ImportError:
    requests = None

try:
    from bs4 import BeautifulSoup
except ImportError:
    BeautifulSoup = None

# =========================================
# CONFIG
# =========================================
SCRIPT_DIR = Path(__file__).parent
ARTICLES_FILE = SCRIPT_DIR / "articles.json"
INDEX_FILE = SCRIPT_DIR / "index.html"
MODEL = "claude-sonnet-4-6"             # Nuanced gates (editorial, research lens)
MODEL_FAST = "claude-haiku-4-5-20251001" # Fast gates (interest, headline, compliance, SEO)
DEFAULT_MIN_SCORE = 7       # Only publish articles scoring this or higher
MAX_ARTICLES_PER_RUN = 5    # Cap per run to keep quality high
MAX_TOTAL_ARTICLES = 200    # Trim old articles beyond this
PIPELINE_TIMEOUT_SECONDS = 420  # 7 minutes — stop processing new articles after this

RSS_FEEDS = []
for i in range(1, 20):
    url = os.environ.get(f"MOLD_REPORT_RSS_{i}", "")
    if url:
        RSS_FEEDS.append(url)
if not RSS_FEEDS:
    single = os.environ.get("MOLD_REPORT_RSS", "")
    if single and "YOUR_FEED_ID" not in single:
        RSS_FEEDS.append(single)

# =========================================
# KNOWLEDGE CORPUS (loaded once at startup)
# =========================================
# Compact corpus injected into every agent system prompt.
# Full corpus (knowledge_corpus.json) available for deep reference.
# Authority: MoldCo Master Claims > Shoemaker Research > CIRS Framework > General news
_CORPUS_COMPACT = ""
_corpus_file = SCRIPT_DIR / "knowledge_compact.json"
if _corpus_file.exists():
    with open(_corpus_file) as _f:
        _corpus_data = json.load(_f)
        _CORPUS_COMPACT = json.dumps(_corpus_data, separators=(',', ':'))
    print(f"✓ Knowledge corpus loaded ({len(_CORPUS_COMPACT)} chars)")
else:
    print("⚠ knowledge_compact.json not found — agents will use built-in rules only")

def get_corpus_context():
    """Return the compact knowledge corpus as a system prompt injection."""
    if _CORPUS_COMPACT:
        return f"\n\nKNOWLEDGE CORPUS (authority hierarchy — Master Claims > Shoemaker Research > CIRS Framework):\n{_CORPUS_COMPACT}\n"
    return ""

# PubMed eutils search queries (fetches recent peer-reviewed research)
PUBMED_SEARCHES = [
    "mold illness OR mycotoxin exposure OR chronic inflammatory response syndrome OR water-damaged building",
    "TGF-beta1 mold OR MMP-9 biotoxin OR MSH mold OR Stachybotrys health",
]
PUBMED_DAYS_BACK = 30   # Only fetch articles from last 30 days
PUBMED_MAX_PER_QUERY = 5

# Government & institutional RSS feeds
# NOTE: Most US gov sites (EPA, CDC, NIH) block automated RSS access.
# For government sources, use Google Alerts instead. Add these alerts:
#   - "EPA mold" (News sources only)
#   - "CDC mold OR indoor air quality" (News sources only)
#   - "mold remediation regulation" (News sources only)
# Then add the feed URLs as MOLD_REPORT_RSS_4, _5, etc. in .env
GOV_RSS_FEEDS = [
    # Add working gov RSS feeds here as you find them
    # Most gov feeds need to come through Google Alerts instead
]
# Keywords to filter government feeds (only keep mold-relevant articles)
GOV_FILTER_KEYWORDS = [
    "mold", "mould", "mycotoxin", "indoor air", "water damage",
    "air quality", "remediation", "Stachybotrys", "Aspergillus",
    "fungal", "fungi", "dampness", "moisture", "sick building",
    "biotoxin", "environmental health", "housing safety",
]

# =========================================
# IMAGE URL VALIDATION
# =========================================
# Dead image domains — services that have shut down or changed their API.
# Any URL matching these gets replaced with a category fallback automatically.
BLOCKED_IMAGE_DOMAINS = [
    "source.unsplash.com",     # Shut down 2023 — use images.unsplash.com instead
    "placehold.co",            # Placeholder service, not real images
    "placeholder.com",         # Placeholder service
    "via.placeholder.com",     # Placeholder service
    "dummyimage.com",          # Placeholder service
    "placekitten.com",         # Placeholder service
]

def validate_image_url(url, category="default"):
    """Validate an image URL. Returns the URL if valid, or a category fallback if not.

    Catches:
    - Dead/blocked domains (source.unsplash.com, placeholder services)
    - Empty or malformed URLs
    - Non-HTTP URLs

    This is the single chokepoint — every image URL passes through here
    before being saved to articles.json.
    """
    if not url or not isinstance(url, str):
        return FALLBACK_IMAGES.get(category, FALLBACK_IMAGES.get("default", ""))

    url = url.strip()

    # Must be a real HTTP(S) URL
    if not url.startswith("http://") and not url.startswith("https://"):
        return FALLBACK_IMAGES.get(category, FALLBACK_IMAGES.get("default", ""))

    # Block known-dead domains
    for domain in BLOCKED_IMAGE_DOMAINS:
        if domain in url:
            print(f"    ⚠ Blocked dead image domain: {domain}")
            return FALLBACK_IMAGES.get(category, FALLBACK_IMAGES.get("default", ""))

    return url


# Image pools: each category and topic has a pool of validated Unsplash
# photo URLs. The picker (see _pick_image) chooses the least-used URL in the
# pool given existing articles, with a deterministic tiebreak by article id.
# This avoids the "9 articles share the same apartment photo" pattern.
#
# All photo IDs in these pools have been validated as live in the corpus or
# confirmed via Unsplash. Adding a brand-new ID without verification is the
# main way these pools degrade over time.

_UNSPLASH = "https://images.unsplash.com/photo-{}?w=800&q=80"

FALLBACK_POOLS = {
    "research": [
        _UNSPLASH.format(s) for s in [
            "1614935151651-0bea6508db6b", "1602052577122-f73b9710adba",
            "1576086213369-97a306d36557", "1576091160550-2173dba999ef",
            "1579154204601-01588f351e67", "1518152006812-edab29b069ac",
            "1532187863486-abf9dbad1b69", "1486825586573-7131f7991bdd",
        ]
    ],
    "regulation": [
        _UNSPLASH.format(s) for s in [
            "1636652966850-5ac4d02370e9", "1623008946073-ad1c850ad0dd",
            "1611326268719-55a69e4316b9", "1688417486337-b089fa4ee670",
            "1592066575517-58df903152f2", "1586773860418-d37222d8fce3",
            "1475875518799-44f63f828ab8",
        ]
    ],
    "news": [
        _UNSPLASH.format(s) for s in [
            "1460317442991-0ec209397118", "1475875518799-44f63f828ab8",
            "1498811008858-d95a730b2ffc", "1545324418-cc1a3fa10c00",
            "1586773860418-d37222d8fce3", "1636409305041-3bd4fe738236",
            "1727767579106-c30a4c1c74c5", "1592066575517-58df903152f2",
        ]
    ],
    "industry": [
        _UNSPLASH.format(s) for s in [
            "1479839672679-a46483c0e7c8", "1518152006812-edab29b069ac",
            "1727767579106-c30a4c1c74c5", "1664190052947-c436bf0d9719",
            "1664190053321-4ef213299eec", "1532187863486-abf9dbad1b69",
            "1545324418-cc1a3fa10c00", "1636409305041-3bd4fe738236",
        ]
    ],
    "diagnostics": [
        _UNSPLASH.format(s) for s in [
            "1602052577122-f73b9710adba", "1576091160550-2173dba999ef",
            "1486825586573-7131f7991bdd", "1614935151651-0bea6508db6b",
            "1576086213369-97a306d36557", "1579154204601-01588f351e67",
        ]
    ],
    "default": [
        _UNSPLASH.format(s) for s in [
            "1649777882133-525e923fd5d7", "1651752523215-9bf678c29355",
            "1664190053321-4ef213299eec", "1664190052947-c436bf0d9719",
        ]
    ],
}

# Back-compat alias: callers expecting a string get the first entry.
FALLBACK_IMAGES = {k: v[0] for k, v in FALLBACK_POOLS.items()}

# Topic-specific pools. Topic detection (_detect_topic) maps article keywords
# to one of these. Pools should be 4+ entries to avoid duplication.
TOPIC_IMAGES = {
    "apartment":  [_UNSPLASH.format(s) for s in [
        "1651752523215-9bf678c29355", "1512917774080-9991f1c4c750",
        "1545324418-cc1a3fa10c00", "1460317442991-0ec209397118",
        "1498811008858-d95a730b2ffc", "1664190053321-4ef213299eec",
    ]],
    "school":     [_UNSPLASH.format(s) for s in [
        "1591123120675-6f7f1aae0e5b", "1498243691581-b145c3f54a5a",
        "1592066575517-58df903152f2", "1586773860418-d37222d8fce3",
    ]],
    "hospital":   [_UNSPLASH.format(s) for s in [
        "1479839672679-a46483c0e7c8", "1519494026892-80bbd2d6fd0d",
        "1576091160550-2173dba999ef", "1486825586573-7131f7991bdd",
    ]],
    "courthouse": [_UNSPLASH.format(s) for s in [
        "1636652966850-5ac4d02370e9", "1623008946073-ad1c850ad0dd",
        "1688417486337-b089fa4ee670",
    ]],
    "laboratory": [_UNSPLASH.format(s) for s in [
        "1614935151651-0bea6508db6b", "1602052577122-f73b9710adba",
        "1576086213369-97a306d36557", "1518152006812-edab29b069ac",
        "1579154204601-01588f351e67",
    ]],
    "government": [_UNSPLASH.format(s) for s in [
        "1611326268719-55a69e4316b9", "1580415742185-c068be2aaecd",
        "1592066575517-58df903152f2", "1475875518799-44f63f828ab8",
    ]],
    "prison":     [_UNSPLASH.format(s) for s in [
        "1627571615836-4948060412a9", "1611326268719-55a69e4316b9",
    ]],
    "police":     [_UNSPLASH.format(s) for s in [
        "1600081191763-05da665acf1a", "1611326268719-55a69e4316b9",
    ]],
    "flooding":   [_UNSPLASH.format(s) for s in [
        "1547683905-f686c993aae5", "1592066575517-58df903152f2",
    ]],
    "military":   [_UNSPLASH.format(s) for s in [
        "1580415742185-c068be2aaecd", "1611326268719-55a69e4316b9",
    ]],
    "mold":       [_UNSPLASH.format(s) for s in [
        "1649777882133-525e923fd5d7", "1664190052947-c436bf0d9719",
        "1664190053321-4ef213299eec", "1576086213369-97a306d36557",
    ]],
    "office":     [_UNSPLASH.format(s) for s in [
        "1518152006812-edab29b069ac", "1727767579106-c30a4c1c74c5",
        "1532187863486-abf9dbad1b69", "1664190052947-c436bf0d9719",
    ]],
    "conference": [_UNSPLASH.format(s) for s in [
        "1727767579106-c30a4c1c74c5", "1545324418-cc1a3fa10c00",
        "1636409305041-3bd4fe738236",
    ]],
}

# =========================================
# COMPLIANCE RULES (from claims-compliance skill)
# =========================================
COMPLIANCE_RULES = """
## MoldCo Claims Compliance Rules for The Mold Report

These rules MUST be followed when writing or reviewing any article:

### CRITICAL: Language Rules
1. MoldCo does NOT diagnose anything. Never say "diagnostic criteria" or "comprehensive CIRS evaluation."
2. Use "mold toxicity treatment" not "CIRS treatment"
3. Use "guided by Shoemaker Protocol" not "following" it
4. Use "may," "might," "research suggests" for health claims. Never state mold causes X as absolute fact.
5. Use "mold-related illness" in patient-facing copy, not "CIRS"
6. Never mention VCS testing
7. Never say "Shoemaker-licensed" or "certified Shoemaker practitioner"
8. Never use sensitivity/specificity stats applied to MoldCo
9. Genetic susceptibility = 24% (never 25% or 30%)
10. Commercial buildings with water damage = 85%. Residential = 50%.
11. Mold growth timeline = 24-48 hours (not 24-72)
12. Use "leading" or "dedicated" not "only" for MoldCo positioning
13. No conspiratorial or accusatory framing about government agencies
14. TGF-B1 is a cytokine/growth factor, NOT a hormone
15. Use "treatment" or "improvement" not "recovery" (less definitive)
16. Colesevelam (generic) before Welchol (brand) when mentioned
17. Never recommend urine mycotoxin testing proactively
18. Blood biomarker testing (TGF-B1, MMP-9, MSH) is the preferred approach

### Shoemaker Alignment
- Frame Shoemaker research positively as foundational, peer-reviewed science
- Reference "30+ years of research" and "14,000+ patients" when relevant
- Don't dismiss mainstream medicine. Frame it as "emerging awareness"
- Position blood biomarkers as objective, evidence-based approach
- Acknowledge that immune-mediated mold illness is distinct from direct toxicity

### Article Tone
- Authoritative but not alarmist
- Data-driven: cite specific numbers, studies, sources
- Never use fear-mongering language
- Frame problems with solutions ("here's what the data shows" not "you're in danger")
- Respect the reader's intelligence
"""


# =========================================
# CLAUDE API WRAPPER
# =========================================
def call_claude(system_prompt, user_prompt, max_tokens=2000, model=None):
    """Call Claude API as a sub-agent. Pass model=MODEL_FAST for cheap/fast gates."""
    if anthropic is None:
        print("  ⚠ anthropic SDK not installed. Run: pip install anthropic")
        return None
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("  ⚠ ANTHROPIC_API_KEY not set.")
        return None
    import time
    use_model = model or MODEL
    client = anthropic.Anthropic(api_key=api_key, timeout=30.0)
    for attempt in range(3):
        try:
            message = client.messages.create(
                model=use_model,
                max_tokens=max_tokens,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}]
            )
            return message.content[0].text
        except anthropic.APIStatusError as e:
            if e.status_code == 529 and attempt < 2:
                print(f"  ⚠ API overloaded, retrying ({attempt+1}/3)...")
                time.sleep(2 * (attempt + 1))
                continue
            print(f"  ⚠ Claude API error: {e}")
            return None
        except Exception as e:
            print(f"  ⚠ Claude API error: {e}")
            return None


def strip_json_fences(text):
    """Strip markdown code fences from AI responses before JSON parsing."""
    import re
    if text:
        text = re.sub(r'^```\w*\s*', '', text.strip())
        text = re.sub(r'\s*```\s*$', '', text)
    return text


# =========================================
# GATE 1: FRESHNESS
# =========================================
def freshness_gate(article):
    """Reject articles older than 90 days or with no verifiable date."""
    pub = article.get('publishedAt', '')
    if not pub:
        print(f"    ✗ REJECTED: No publication date")
        return False
    try:
        pub_dt = datetime.fromisoformat(pub.replace('Z', '+00:00'))
        age_days = (datetime.now(timezone.utc) - pub_dt).days
        if age_days > 90:
            print(f"    ✗ REJECTED: Article is {age_days} days old (max 90)")
            return False
        if age_days < 0:
            print(f"    ✗ REJECTED: Future date detected ({pub})")
            return False
        print(f"    ✓ Fresh: {age_days} days old")
        return True
    except (ValueError, TypeError):
        print(f"    ✗ REJECTED: Invalid date format ({pub})")
        return False


# =========================================
# GATE 2: SOURCE VERIFICATION
# =========================================
def source_verification_gate(article):
    """Reject articles without a verifiable source URL."""
    url = article.get('sourceUrl', '').strip()
    if not url:
        print(f"    ✗ REJECTED: No source URL")
        return False
    if not url.startswith('http'):
        print(f"    ✗ REJECTED: Invalid source URL ({url})")
        return False
    blocked_domains = ['example.com', 'fake', 'placeholder', 'test.com']
    from urllib.parse import urlparse
    domain = urlparse(url).netloc.lower()
    if any(b in domain for b in blocked_domains):
        print(f"    ✗ REJECTED: Blocked domain ({domain})")
        return False
    print(f"    ✓ Source: {domain}")
    return True


# =========================================
# GATE 3: INTEREST SCORING (auto-gate)
# =========================================
def interest_agent(article):
    """Score how interesting/newsworthy an article is. Returns the article with _interest_score."""
    print(f"  ★ Interest: {article['title'][:50]}...")

    system = """You are the editorial judgment agent for The Mold Report, the first AI-curated mold news publication grounded in Dr. Ritchie Shoemaker's body of research on CIRS (Chronic Inflammatory Response Syndrome).

Our editorial lens: mold illness is an innate immune response to biotoxin exposure in genetically susceptible individuals — NOT a fungal infection, NOT an allergy, NOT a simple respiratory irritant. Every article we publish must be connectable to this understanding.

Score how INTERESTING and NEWSWORTHY an article is on a 1-10 scale:

HIGHLY INTERESTING (8-10):
- New peer-reviewed research on inflammatory biomarkers, immune response to mold/biotoxins
- Studies involving TGF-beta1, MMP-9, MSH, C4a, VIP, VEGF, or other CIRS-related markers
- Government regulation changes (new laws, standards, enforcement for water-damaged buildings)
- Large-scale health impacts (school closures, housing crises, class actions from mold exposure)
- Research on genetic susceptibility to mold illness (HLA-DR genes)
- Indoor environmental quality studies (water-damaged buildings, remediation standards)
- Patient rights, housing safety, institutional accountability for mold conditions

MODERATELY INTERESTING (5-7):
- Local mold news with broader implications for building safety or patient advocacy
- Conference findings or expert commentary on mold illness
- Industry reports with new data on water damage, remediation, or building health
- Updates to existing stories our audience follows

AUTOMATIC REJECTION (score 1-2):
- Articles about FUNGAL INFECTIONS (aspergillosis, invasive fungal disease, antifungals, nail fungus, candida, athlete's foot) — this is infectious disease, NOT mold illness
- Articles about MOLD ALLERGIES or allergic reactions (IgE-mediated response) — this is allergy, NOT CIRS
- Articles about FOOD MOLD or food safety
- Antifungal drug research or nanoparticle antifungals
- Agricultural/crop mold or plant pathology
- General microbiology with no connection to human mold illness from buildings
- Promotional content disguised as news
- Listicles ("10 signs of mold") with no original reporting

LOW INTEREST (3-4):
- Generic "mold is bad" articles with no new information
- Local stories with no broader relevance
- Rehashed information without new data

Return ONLY valid JSON:
{"score": 1-10, "reasoning": "one sentence why", "headline_hook": "suggested angle if score >= 6"}

Be very selective. We publish quality over quantity. If an article is about fungal infection or allergy, score it 1-2 regardless of how interesting the science is — it's not our lens."""

    prompt = f"""Score this article's editorial interest:

Title: {article['title']}
Summary: {article['summary'][:400]}
Source: {article['source']}
Category: {article['category']}"""

    result = call_claude(system + get_corpus_context(), prompt, max_tokens=200, model=MODEL_FAST)
    if result:
        try:
            json_match = re.search(r'\{.*\}', strip_json_fences(result), re.DOTALL)
            if json_match:
                review = json.loads(json_match.group())
                score = review.get('score', 5)
                article['_interest_score'] = score
                article['_interest_reasoning'] = review.get('reasoning', '')
                print(f"    {'★' * min(score, 10)} {score}/10 — {review.get('reasoning', '')[:60]}")
        except (json.JSONDecodeError, AttributeError):
            article['_interest_score'] = 5
    else:
        article['_interest_score'] = 5
    return article


# =========================================
# AGENT: TIP SCREENING (reader submissions)
# =========================================
def tip_screening_agent(article):
    """Screen reader-submitted tips for editorial validity and Shoemaker/MoldCo alignment.
    Tips without source URLs get extra scrutiny. Returns article with _tip_approved flag."""
    if article.get('_source_type') != 'reader_tip':
        article['_tip_approved'] = True
        return article

    print(f"  🔍 Tip screening: {article['title'][:50]}...")

    system = f"""You are the tip screening agent for The Mold Report, the first AI-curated mold news publication.

Your job is to evaluate reader-submitted news tips for:
1. EDITORIAL VALIDITY — Is this a real, newsworthy development? Or is it spam, self-promotion, misinformation, or a personal anecdote that isn't news?
2. SHOEMAKER/MOLDCO ALIGNMENT — Does this story align with or at least not contradict the Shoemaker Protocol framework and MoldCo's mission? We don't publish content that:
   - Promotes urine mycotoxin testing as a primary diagnostic
   - Pushes unproven "detox" products or supplements without clinical evidence
   - Uses conspiratorial framing about government agencies
   - Contradicts established Shoemaker Protocol research
   - Promotes competing non-evidence-based mold illness frameworks
3. VERIFIABILITY — Can the claims in this tip be verified? Tips with source URLs are easier to verify. Tips without URLs need stronger internal evidence (specific names, dates, institutions, data points).

{COMPLIANCE_RULES}

Return ONLY valid JSON:
{{"approved": true/false, "confidence": 0.0-1.0, "reasoning": "one sentence", "concerns": ["concern 1", ...], "suggested_category": "research|regulation|news|industry|diagnostics"}}

Be selective but fair. Reader tips are valuable — many great stories come from the community. But we must protect editorial standards."""

    has_url = bool(article.get('sourceUrl', '').strip())
    prompt = f"""Screen this reader-submitted tip:

Title: {article['title']}
Summary: {article['summary'][:500]}
Category (submitter chose): {article['category']}
Source URL: {article.get('sourceUrl', 'NONE PROVIDED')}
Submitter: {article.get('_submitter_name', 'Anonymous')}

{'Note: No source URL was provided. Apply extra scrutiny to verifiability.' if not has_url else 'Source URL provided — verify alignment and editorial value.'}"""

    result = call_claude(system, prompt, max_tokens=300)
    if result:
        try:
            json_match = re.search(r'\{.*\}', strip_json_fences(result), re.DOTALL)
            if json_match:
                review = json.loads(json_match.group())
                approved = review.get('approved', False)
                confidence = review.get('confidence', 0.5)
                article['_tip_approved'] = approved and confidence >= 0.6
                article['_tip_screening'] = review

                # Update category if agent suggests a better one
                if review.get('suggested_category'):
                    article['category'] = review['suggested_category']

                if article['_tip_approved']:
                    print(f"    ✓ Tip approved (confidence: {confidence}) — {review.get('reasoning', '')[:60]}")
                else:
                    concerns = review.get('concerns', [])
                    print(f"    ✗ Tip rejected (confidence: {confidence}) — {review.get('reasoning', '')[:60]}")
                    if concerns:
                        print(f"      Concerns: {', '.join(concerns[:3])}")
        except (json.JSONDecodeError, AttributeError):
            print("    ⚠ Could not parse screening response — rejecting tip by default")
            article['_tip_approved'] = False
    else:
        article['_tip_approved'] = False
    return article


# =========================================
# AGENT: HEADLINE REWRITE
# =========================================
def headline_agent(article):
    """Rewrite article titles to be compelling and click-worthy, especially for research papers."""
    print(f"  ✏ Headline: {article['title'][:50]}...")

    original_title = article['title']

    system = """You are the headline editor for The Mold Report, the first AI-curated mold news publication.

Your job is to rewrite article titles to make people WANT to read. You're writing for a smart audience that cares about mold exposure, health, and science — but they still need a reason to click.

RULES FOR GREAT HEADLINES:
1. Lead with the most interesting finding or implication, not the method
2. Include the core searchable term naturally — if the article is about black mold, say "black mold" not just "mold"
3. Use plain language. Never use academic jargon in the headline
4. Be specific: include numbers, names, biomarkers, institutions when they add punch
5. Keep it under 90 characters (strict limit)
6. Never use clickbait ("You won't believe..."), all-caps, or exclamation marks
7. Never misrepresent the content — accuracy is sacred
8. Use active voice. "Study Finds X" not "X Was Found By Study"
9. For research papers: translate the finding into what it MEANS for real people
10. Drop unnecessary words (a, the, that) where it reads naturally
11. Front-load searchable terms: put the keyword phrase in the first half of the headline
12. Include condition/symptom names when relevant: "fatigue", "brain fog", "respiratory illness"

EXAMPLES OF GOOD REWRITES:
- BEFORE: "Combined toxicity prediction of deoxynivalenol and fumonisin B(1) by physiologically based toxicokinetic modelling"
  AFTER: "Two Common Grain Mold Toxins Are More Dangerous Together, New Model Shows"

- BEFORE: "Human Contact Frequency as a Dominant Ecological Driver of Fungal Community Assembly"
  AFTER: "The Surfaces You Touch Most Are the Biggest Mold Highways, Study Finds"

- BEFORE: "Indoor air bacterial and fungal burden in the environment of an atopic child"
  AFTER: "Home Air Quality May Drive Measurable Mycotoxin Levels in Children With Allergies"

- BEFORE: "EPA Issues Updated Guidelines for Mold Assessment in Schools"
  AFTER: Same — this is already good. Don't rewrite headlines that already work.

Return ONLY valid JSON:
{"rewritten": "the new headline", "changed": true/false, "reasoning": "why you changed it or kept it"}

If the original is already compelling and clear, set changed to false and return the original."""

    prompt = f"""Rewrite this headline for The Mold Report:

Original title: {article['title']}
Category: {article['category']}
Summary excerpt: {article['summary'][:300]}
Source: {article['source']}"""

    result = call_claude(system, prompt, max_tokens=200, model=MODEL_FAST)
    if result:
        try:
            json_match = re.search(r'\{.*\}', strip_json_fences(result), re.DOTALL)
            if json_match:
                review = json.loads(json_match.group())
                if review.get('changed', False) and review.get('rewritten'):
                    new_title = review['rewritten'].strip()
                    if len(new_title) <= 100 and len(new_title) > 10:
                        article['title'] = new_title
                        article['_original_title'] = original_title
                        print(f"    → \"{new_title}\"")
                    else:
                        print(f"    → Kept original (rewrite too {'long' if len(new_title) > 100 else 'short'})")
                else:
                    print(f"    → Kept original (already good)")
        except (json.JSONDecodeError, AttributeError):
            print("    ⚠ Could not parse headline response — keeping original")
    return article


# =========================================
# AGENT: EDITORIAL REWRITE
# =========================================
def editorial_agent(article):
    """Rewrite article summary in Mold Report editorial voice."""
    print(f"  ✎ Editorial: {article['title'][:50]}...")

    system = """You are the editorial voice of The Mold Report, the first AI-curated mold news publication.
Your job is to rewrite article summaries in a clear, authoritative, data-driven voice.

Style rules:
- 2-3 paragraphs, 150-250 words total
- Lead with the most important finding or development
- Include specific data points (numbers, dates, institutions)
- Neutral, journalistic tone. Not promotional. Not alarmist.
- Short sentences. Clear structure. No jargon without explanation.
- Never use em dashes. Use periods and commas.
- End with context: why this matters for people concerned about mold exposure.

CRITICAL — Respect the source material:
Your summary must accurately reflect the original article's content. Do NOT silently inject claims, recommendations, or context that was not in the original source. The summary should be a rewrite of what the source actually reported.

Do NOT add context that was not in the original article. Just rewrite what was reported.

Return ONLY valid JSON in this format:
{"summary": "the rewritten summary text"}

Do NOT add any editors notes or additional context. Just rewrite the summary."""

    prompt = f"""Rewrite this article for The Mold Report:

Title: {article['title']}
Original summary: {article['summary']}
Source: {article['source']}
Category: {article['category']}"""

    result = call_claude(system, prompt, max_tokens=300)
    if result:
        try:
            json_match = re.search(r'\{.*\}', strip_json_fences(result), re.DOTALL)
            if json_match:
                parsed = json.loads(json_match.group())
                if parsed.get('summary'):
                    summary = parsed['summary'].strip()
                    # Enforce max 300 words (asked for 150-250)
                    words = summary.split()
                    if len(words) > 300:
                        summary = ' '.join(words[:280])
                        print(f"    ⚠ Trimmed summary from {len(words)} to 280 words")
                    article['summary'] = summary
                    article['readTime'] = max(2, round(len(words) / 200) + 2)

            else:
                # Fallback: treat entire result as summary (backward compat)
                article['summary'] = result.strip()
                article['readTime'] = max(2, round(len(result.split()) / 200) + 2)
        except (json.JSONDecodeError, AttributeError):
            # Fallback: treat entire result as summary
            article['summary'] = result.strip()
            article['readTime'] = max(2, round(len(result.split()) / 200) + 2)
    return article


# =========================================
# COMPLIANCE LINT (deterministic, no API needed)
# =========================================
# Hard checks that block publish. These mirror the most-cited rules from the
# claims-compliance skill. Every entry: a "trigger" pattern, an optional
# "rescue" pattern that, if also present in the article, signals the trigger
# is being discussed responsibly (with the MoldCo position) rather than
# violating the rule.
#
# Hard violations CANNOT be silently auto-corrected because they typically
# imply a missing position statement, not just a wrong word.

COMPLIANCE_HARD_RULES = [
    {
        "name": "urine mycotoxin testing without skeptical framing",
        "trigger": re.compile(r"\b(urine|urinary)\s+mycotoxin\b", re.I),
        "rescue": re.compile(
            r"(not\s+(useful|diagnostic|endorsed|validated)|"
            r"lacks?\s+validated|cannot\s+distinguish|"
            r"blood\s+biomarkers?|drawn\s+caution|CDC\s+caution|"
            r"contested|unresolved|debated|not\s+a\s+useful)",
            re.I,
        ),
        "message": (
            "Article mentions urine mycotoxin testing without the MoldCo "
            "position. Per claims-compliance: it is not a useful diagnostic "
            "for mold illness from water-damaged buildings -- cannot "
            "distinguish food vs. inhaled exposure, lacks validated reference "
            "ranges, not diagnostic for CIRS. Add an editor's note + body "
            "language pointing to blood biomarkers (TGF-\u03b21, MMP-9, MSH)."
        ),
    },
    {
        "name": "VCS / visual contrast sensitivity",
        "trigger": re.compile(r"\b(VCS|visual\s+contrast\s+sensitivity)\b", re.I),
        "rescue": None,  # never allowed
        "message": "Per claims-compliance Rule #6: never mention VCS testing.",
    },
    {
        "name": "Shoemaker-licensed / certified Shoemaker",
        "trigger": re.compile(
            r"(shoemaker[-\s]licensed|certified\s+shoemaker|shoemaker[-\s]certified)",
            re.I,
        ),
        "rescue": None,
        "message": (
            "Per claims-compliance Rule #4: certification is not currently "
            "available. Use 'trained in Dr. Shoemaker's Protocol' instead."
        ),
    },
    {
        "name": "wrong genetic susceptibility stat",
        "trigger": re.compile(
            r"\b(25|30)\s*%\s*(of\s+(the\s+)?population|with\s+HLA|genetic|susceptibility)",
            re.I,
        ),
        "rescue": None,
        "message": "Per claims-compliance Rule #8: genetic susceptibility = 24%, never 25% or 30%.",
    },
    {
        "name": "wrong mold growth timeline",
        "trigger": re.compile(r"\b24\s*[\u2013-]\s*72\s*hours?\b"),
        "rescue": None,
        "message": "Per claims-compliance Rule #8: mold growth timeline = 24-48 hours, not 24-72.",
    },
    {
        "name": "diagnostic accuracy claim applied to MoldCo",
        "trigger": re.compile(
            r"(98\.[45]\s*%\s*sensitivity|"
            r"diagnostic\s+accuracy|"
            r"MoldCo\s+diagnoses|"
            r"diagnose[sd]?\s+CIRS)",
            re.I,
        ),
        "rescue": None,
        "message": (
            "Per claims-compliance Rule #1 / #9: MoldCo does NOT diagnose. "
            "No sensitivity/specificity stats applied to MoldCo's approach."
        ),
    },
    {
        "name": "modernized version of Shoemaker",
        "trigger": re.compile(r"modernized\s+version\s+of\s+(the\s+)?shoemaker", re.I),
        "rescue": None,
        "message": (
            "Per claims-compliance Rule #3: do not call MoldCo a 'modernized "
            "version' of the Shoemaker Protocol. Use 'guided by Shoemaker Protocol'."
        ),
    },
]

# Soft warnings -- log but don't block. Catch lower-risk drift.
COMPLIANCE_SOFT_RULES = [
    {
        "name": "uses 'follow' instead of 'guided by' near Shoemaker",
        "trigger": re.compile(r"follow(s|ing|ed)?\s+(the\s+)?shoemaker", re.I),
        "message": "Prefer 'guided by Shoemaker Protocol' over 'following' it.",
    },
    {
        "name": "TGF-B1 referred to as a hormone",
        "trigger": re.compile(r"TGF[-\s]?(\u03b2|B|b)1[^.]{0,40}\bhormone\b", re.I),
        "message": "TGF-\u03b21 is a cytokine / growth factor, not a hormone.",
    },
]


def compliance_lint(article):
    """Deterministic compliance lint. Runs without API access.

    Returns (hard_violations, soft_warnings) where each is a list of dicts:
    {"name": str, "message": str, "match": str}.

    Hard violations should BLOCK publishing. Soft warnings are advisory.
    """
    blob_parts = [
        article.get("title", "") or "",
        article.get("summary", "") or "",
        article.get("editorsNote", "") or "",
        " ".join(article.get("tags", []) or []),
    ]
    blob = "\n".join(blob_parts)

    hard = []
    for rule in COMPLIANCE_HARD_RULES:
        m = rule["trigger"].search(blob)
        if not m:
            continue
        if rule.get("rescue") and rule["rescue"].search(blob):
            continue  # discussed responsibly with the MoldCo position present
        hard.append({"name": rule["name"], "message": rule["message"], "match": m.group(0)})

    soft = []
    for rule in COMPLIANCE_SOFT_RULES:
        m = rule["trigger"].search(blob)
        if m:
            soft.append({"name": rule["name"], "message": rule["message"], "match": m.group(0)})

    return hard, soft


# =========================================
# AGENT: COMPLIANCE CHECK
# =========================================
def compliance_agent(article):
    """Check article against MoldCo compliance rules. Auto-corrects issues."""
    print(f"  ⚖ Compliance: {article['title'][:50]}...")

    system = f"""You are the compliance reviewer for The Mold Report.
Check the article against these rules and return a JSON response.

{COMPLIANCE_RULES}

Return ONLY valid JSON in this exact format:
{{"pass": true/false, "issues": ["issue 1", "issue 2"], "corrected_summary": "..."}}

If pass is true, corrected_summary should be an empty string.
If pass is false, fix terminology and compliance issues directly in corrected_summary.
Do NOT add editorial context, biomarker recommendations, or protocol explanations. Just fix the language."""


    prompt = f"""Review this article for compliance:

Title: {article['title']}
Summary: {article['summary']}
Category: {article['category']}
Tags: {', '.join(article.get('tags', []))}"""

    article['_compliance_pass'] = True  # default: pass if API/parsing fails
    result = call_claude(system, prompt, max_tokens=800, model=MODEL_FAST)
    if result:
        try:
            json_match = re.search(r'\{.*\}', strip_json_fences(result), re.DOTALL)
            if json_match:
                review = json.loads(json_match.group())
                if not review.get('pass', True):
                    print(f"    ⚠ Issues found: {review.get('issues', [])}")
                    if review.get('corrected_summary'):
                        article['summary'] = review['corrected_summary']
                        print("    ✓ Auto-corrected")
                    article['_compliance_pass'] = True  # auto-corrected = pass
                else:
                    print("    ✓ Passed")
                article['_compliance_issues'] = review.get('issues', [])
        except (json.JSONDecodeError, AttributeError):
            print("    ⚠ Could not parse compliance response — passing by default")
    return article


# =========================================
# AGENT: SHOEMAKER RESEARCH LENS (most important gate)
# =========================================
def research_agent(article):
    """The Shoemaker Research Lens — the most critical gate in the pipeline.

    Every research/diagnostics article gets contextualized through the body of work
    Dr. Ritchie Shoemaker published over 30+ years. This agent doesn't just verify —
    it actively connects new findings to Shoemaker's published research, adding
    editor's notes that ground each article in the foundational science.

    This is what makes The Mold Report unique: every piece of mold science news
    is interpreted through the lens of the most comprehensive body of research
    on mold illness ever published.
    """
    # Run Shoemaker Lens on ALL categories — research gets full enrichment,
    # news/regulation get alignment check to filter off-topic content
    print(f"  🔬 Shoemaker Lens: {article['title'][:50]}...")

    system = """You are the Shoemaker Research Analyst for The Mold Report. You are the GATEKEEPER — the most important agent in the entire pipeline. You decide what gets published and what gets killed.

Your expertise is the full body of published research by Dr. Ritchie Shoemaker, spanning 30+ years and 14,000+ patients. You know his published papers, his biomarker cascade model, and the clinical evidence for CIRS (Chronic Inflammatory Response Syndrome) as an immune-mediated illness triggered by biotoxin exposure in genetically susceptible individuals (24% of the population carry HLA-DR susceptibility genes).

THE SHOEMAKER MODEL OF MOLD ILLNESS:
Mold illness (CIRS) is an INNATE IMMUNE RESPONSE — the body's immune system gets stuck in chronic inflammatory overdrive after biotoxin exposure. It is NOT:
- A fungal INFECTION (fungus growing in/on the body)
- An ALLERGY (IgE-mediated allergic response)
- A simple RESPIRATORY IRRITANT
- Direct TOXICITY from mycotoxins
These are completely different mechanisms. Articles framed around these concepts do NOT belong on The Mold Report.

YOUR TWO JOBS:

JOB 1 — KILL OFF-TOPIC CONTENT (set verified: false):
REJECT any article primarily about:
- Fungal infections: aspergillosis, invasive fungal disease, antifungal drugs/treatments, candida, dermatophytes, nail/skin fungus
- Allergic responses: mold allergy, allergic rhinitis from mold, IgE testing, antihistamine treatment for mold
- Food mold, agricultural mold, crop pathology
- Antifungal drug development, nanoparticle antifungals
- General mycology/microbiology with no human health connection to buildings
- Immunocompromised patients getting fungal infections (this is infectious disease, not CIRS)

JOB 2 — ENRICH ALIGNED CONTENT (set verified: true):
For articles that DO align, connect them to Shoemaker's published work:
- Shoemaker & House 2006: Defined CIRS diagnostic criteria using objective biomarkers
- Shoemaker 2010: SBS-related illness from water-damaged buildings, innate immune response
- Ryan, Shoemaker et al 2024: Comprehensive CIRS treatment review (most recent)
- Dooley & McMahon 2020: Pediatric CIRS treatment outcomes
- The biotoxin pathway: exposure → innate immune activation → cytokine storm → multi-system inflammation
- Key biomarkers: TGF-beta1 (cytokine/growth factor, NOT hormone), MMP-9, MSH, C4a, VIP, VEGF
- 24% genetic susceptibility via HLA-DR genes
- Blood biomarker testing (NOT urine mycotoxin testing)

WHAT GETS THROUGH (verified: true):
- Water-damaged building health effects
- Inflammatory biomarker research connected to mold/biotoxin exposure
- Indoor environmental quality studies
- Patient advocacy, housing safety, institutional accountability
- Mold remediation standards and enforcement
- Genetic susceptibility research
- Multi-system inflammatory conditions linked to environmental exposure
- News about mold in buildings (schools, homes, hospitals, prisons) affecting occupants
- Public figures or celebrities discussing mold illness, CIRS, or biotoxin exposure — these raise awareness and ALWAYS get through
- Personal stories of mold illness that align with the innate immune model — thin sourcing is NOT a reason to reject awareness content

IMPORTANT: Your job is topic alignment, not source quality. If an article is ABOUT mold illness / CIRS / biotoxin exposure and frames it correctly, let it through. Do not reject articles for thin sourcing, celebrity framing, or lack of clinical data. The interest agent already scored relevance — you are checking ALIGNMENT only.

COMPLIANCE RULES FOR EDITORS NOTES:
- Use "research suggests" or "may" — never absolute claims
- Say "guided by" Shoemaker Protocol, not "following"
- Use "mold-related illness" not "CIRS" in patient-facing context
- TGF-beta1 is a cytokine/growth factor, NOT a hormone
- Never say "diagnostic criteria" — say "research-based biomarker patterns"
- Reference "published research" and "peer-reviewed studies"

Return ONLY valid JSON:
{
  "verified": true/false,
  "alignment": "shoemaker_aligned" | "neutral" | "off_topic",
  "rejection_reason": "why this was rejected (empty string if verified is true)",
  "editors_note": "One sentence of context only. Write like a real newspaper editor — brief, neutral, informative. No biomarker lists, no protocol explanations, no compliance language. If the article stands on its own, leave this as an empty string. Most articles should NOT have an editors note.",
  "corrections": "corrected summary text if factual errors exist, empty string otherwise",
  "notes": ["any accuracy concerns"]
}

CRITICAL: When in doubt, REJECT. We publish quality over quantity. An article about antifungal nanoparticles has NOTHING to do with CIRS and must be killed. An article about allergic rhinitis from mold is the WRONG LENS and must be killed. Only pass content that our audience — people dealing with chronic multi-system mold illness — will find relevant."""

    prompt = f"""Analyze this research article through the Shoemaker lens:

Title: {article['title']}
Summary: {article['summary']}
Category: {article['category']}
Tags: {', '.join(article.get('tags', []))}
Source: {article['source']}"""

    article['_research_verified'] = True  # default: pass if API/parsing fails
    result = call_claude(system + get_corpus_context(), prompt, max_tokens=600)
    if result:
        try:
            json_match = re.search(r'\{.*\}', strip_json_fences(result), re.DOTALL)
            if json_match:
                review = json.loads(json_match.group())
                verified = review.get('verified', True)
                alignment = review.get('alignment', 'neutral')
                article['_research_verified'] = verified
                article['_shoemaker_alignment'] = alignment

                if not verified:
                    reason = review.get('rejection_reason', alignment)
                    print(f"    ✗ KILLED by Shoemaker Lens: {reason[:80]}")
                    return article

                # Store editor's note grounding article in Shoemaker research
                if review.get('editors_note'):
                    note = review['editors_note'].strip()
                    article['editorsNote'] = note

                if review.get('corrections'):
                    article['summary'] = review['corrections']
                    print(f"    ✓ Corrected (alignment: {alignment})")
                else:
                    print(f"    ✓ Verified (alignment: {alignment})")

                if review.get('notes'):
                    for note in review['notes']:
                        if note:
                            print(f"    📝 {note}")
        except (json.JSONDecodeError, AttributeError):
            print("    ⚠ Could not parse research response")
    return article



# =========================================
# AGENT: DUPLICATE DETECTION
# =========================================
def _title_similarity(a, b):
    """Quick title similarity using SequenceMatcher."""
    from difflib import SequenceMatcher
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def _extract_key_entities(text):
    """Extract location names, org names, dollar amounts for comparison."""
    text_lower = text.lower()
    entities = set()
    # Dollar amounts
    for m in re.findall(r'\$[\d,.]+[kmb]?', text_lower):
        entities.add(m)
    # Common named entities (states, cities mentioned in mold articles)
    for word in text.split():
        if len(word) > 3 and word[0].isupper():
            entities.add(word.lower())
    return entities


def duplicate_detection_agent(article, existing_articles):
    """
    DEDUP AGENT — checks if a new article is a duplicate of an existing one.

    Three-layer check:
      1. Same source URL → definite duplicate
      2. High title similarity (>0.7) → likely duplicate
      3. Same topic + same location/entities + close dates → probable duplicate

    Returns:
      article with '_duplicate' flag and '_duplicate_of' ID if duplicate found.
      article['_dedup_pass'] = True means it's NOT a duplicate (good to publish).
    """
    title = article.get('title', '')
    url = article.get('sourceUrl', '')
    summary = article.get('summary', '')
    pub_date = article.get('publishedAt', '')[:10]  # YYYY-MM-DD

    print(f"  🔍 Dedup: checking against {len(existing_articles)} articles...")

    for existing in existing_articles:
        ex_title = existing.get('title', '')
        ex_url = existing.get('sourceUrl', '')
        ex_summary = existing.get('summary', '')

        # Layer 1: Same source URL
        if url and ex_url and url == ex_url:
            print(f"    ✗ DUPLICATE: Same source URL as '{ex_title[:50]}...'")
            article['_dedup_pass'] = False
            article['_duplicate_of'] = existing['id']
            article['_duplicate_reason'] = 'same_url'
            return article

        # Layer 2: High title similarity
        title_sim = _title_similarity(title, ex_title)
        if title_sim > 0.75:
            print(f"    ✗ DUPLICATE: Title {title_sim:.0%} similar to '{ex_title[:50]}...'")
            article['_dedup_pass'] = False
            article['_duplicate_of'] = existing['id']
            article['_duplicate_reason'] = f'title_similarity_{title_sim:.2f}'
            return article

        # Layer 3: Moderate title similarity + overlapping entities + close dates
        if title_sim > 0.5:
            new_entities = _extract_key_entities(title + " " + summary[:200])
            ex_entities = _extract_key_entities(ex_title + " " + ex_summary[:200])
            overlap = new_entities & ex_entities
            if len(overlap) >= 3:
                # Check date proximity (within 7 days)
                ex_date = existing.get('publishedAt', '')[:10]
                try:
                    from datetime import datetime
                    d1 = datetime.strptime(pub_date, '%Y-%m-%d')
                    d2 = datetime.strptime(ex_date, '%Y-%m-%d')
                    days_apart = abs((d1 - d2).days)
                except (ValueError, TypeError):
                    days_apart = 999

                if days_apart <= 14:
                    print(f"    ✗ DUPLICATE: Similar topic ({title_sim:.0%}), "
                          f"{len(overlap)} shared entities, {days_apart}d apart")
                    print(f"      Existing: '{ex_title[:60]}...'")
                    article['_dedup_pass'] = False
                    article['_duplicate_of'] = existing['id']
                    article['_duplicate_reason'] = f'topic_overlap_{len(overlap)}_entities'
                    return article

    print(f"    ✓ Unique article")
    article['_dedup_pass'] = True
    return article


# =========================================
# AGENT: PHOTO
# =========================================
def _detect_topic(title, summary=""):
    """Detect the visual topic of an article for image selection."""
    text = (title + " " + summary).lower()
    topic_keywords = {
        "school":      ["school", "elementary", "university", "college", "student", "campus"],
        "apartment":   ["apartment", "tenant", "housing", "resident", "home", "condo", "cove"],
        "hospital":    ["hospital", "doctor", "medical", "health", "asthma", "er visit", "clinic"],
        "courthouse":  ["lawsuit", "sue", "court", "legal", "attorney", "ruling", "verdict"],
        "laboratory":  ["study", "research", "lab", "toxin", "bacterial", "fungal", "microbiome"],
        "government":  ["city hall", "council", "government", "museum", "oversight", "congress"],
        "prison":      ["prison", "inmate", "prisoner", "correctional", "jail"],
        "police":      ["police", "law enforcement", "precinct"],
        "flooding":    ["flood", "water damage", "hurricane", "storm", "humidity"],
        "military":    ["military", "base", "barracks", "army", "air force"],
    }
    for topic, keywords in topic_keywords.items():
        if any(kw in text for kw in keywords):
            return topic
    return None


def _pick_image(pool, existing_articles, article_id):
    """Pick the least-used image in `pool` given what's already in articles.json.

    Tiebreak deterministically by hashing article_id, so the same article always
    receives the same image even across re-runs.
    """
    if not pool:
        return ""
    if not isinstance(pool, list):
        return pool

    # Count uses per base URL (strip query string for stable comparison)
    def _base(u):
        return (u or "").split("?")[0]

    used = {}
    for a in existing_articles or []:
        b = _base(a.get("imageUrl", ""))
        if b:
            used[b] = used.get(b, 0) + 1

    counted = [(used.get(_base(u), 0), u) for u in pool]
    min_count = min(c for c, _ in counted)
    candidates = [u for c, u in counted if c == min_count]

    if len(candidates) == 1:
        return candidates[0]

    # Deterministic tiebreak: hash article_id and index into candidates
    seed = (article_id or "") + "|imgpick"
    h = sum(ord(c) for c in seed)
    return candidates[h % len(candidates)]


def photo_agent(article, existing_articles=None):
    """Assign images via OG extraction → topic-specific Unsplash → category fallback.

    Picks the least-used image in each pool to avoid the "9 articles share one
    photo" repetition pattern. Pass `existing_articles` (typically
    load_articles()["articles"]) to give the picker visibility into what's
    already on the site. If omitted, the picker loads the corpus itself.

    Every image URL is validated through validate_image_url() before assignment,
    which blocks dead domains (source.unsplash.com etc.) and malformed URLs.
    """
    cat = article.get('category', 'default')

    # If article already has a valid non-Unsplash image (e.g. set by a prior
    # OG fetch), keep it — repetition only matters for fallback pool picks.
    existing = article.get('imageUrl', '')
    if existing and 'unsplash' not in existing:
        article['imageUrl'] = validate_image_url(existing, cat)
        return article

    print(f"  📷 Photo: {article['title'][:50]}...")

    # Lazily load corpus if caller didn't pass one
    if existing_articles is None:
        try:
            existing_articles = load_articles().get("articles", [])
        except Exception:
            existing_articles = []

    aid = article.get("id", "")

    # Layer 1: Try OG image from source
    url = article.get('sourceUrl', '')
    if url and requests and BeautifulSoup:
        try:
            headers = {"User-Agent": "TheMoldReport/1.0"}
            resp = requests.get(url, headers=headers, timeout=5)
            soup = BeautifulSoup(resp.text, "html.parser")
            og = soup.find("meta", property="og:image")
            if og and og.get("content"):
                validated = validate_image_url(og["content"], cat)
                article['imageUrl'] = validated
                article['imageAlt'] = article['title'][:80]
                print(f"    ✓ OG image found")
                return article
        except Exception:
            pass

    # Layer 2: Topic-specific Unsplash image (least-used in its pool)
    topic = _detect_topic(article.get('title', ''), article.get('summary', ''))
    if topic and topic in TOPIC_IMAGES:
        pool = TOPIC_IMAGES[topic]
        article['imageUrl'] = _pick_image(pool, existing_articles, aid)
        article['imageAlt'] = article['title'][:80]
        print(f"    → Topic image ({topic}, pool={len(pool)})")
        return article

    # Layer 3: Category fallback (least-used in pool)
    pool = FALLBACK_POOLS.get(cat, FALLBACK_POOLS.get('default', []))
    if pool:
        article['imageUrl'] = _pick_image(pool, existing_articles, aid)
    else:
        article['imageUrl'] = FALLBACK_IMAGES.get(cat, FALLBACK_IMAGES.get('default', ''))
    article['imageAlt'] = f"{cat.title()} related image"
    print(f"    → Fallback pool ({cat}, pool={len(pool)})")
    return article


# =========================================
# AGENT: SEO OPTIMIZATION
# =========================================
def seo_agent(article):
    """Generate search-optimized meta title and description for each article."""
    print(f"  🔎 SEO: {article['title'][:50]}...")

    system = """You are an SEO specialist for The Mold Report, the first AI-curated mold news publication.
Your job: generate search-optimized metadata that makes each article rank for its topic.

META TITLE (50-60 chars max, including " | The Mold Report" suffix):
- Front-load the primary search keyword
- Optimize for what real people Google, not the academic title
- Include "mold" or a mold-related term when natural
- Use pipe: "Topic Phrase Here | The Mold Report"

META DESCRIPTION (150-160 chars max):
- Summarize the key finding in 1-2 punchy sentences
- Include primary + secondary keywords naturally
- Use specifics: numbers, names, locations, biomarkers
- End with implicit value: why should I click this?
- Never start with "This article" or "In this article"

PRIMARY KEYWORD (2-4 words, the exact phrase you want to rank for):
- This is the #1 search query this article should capture
- Must be something real people actually search for
- Examples: "mold exposure symptoms", "black mold health risks", "mycotoxin testing", "CIRS diagnosis"

SECONDARY KEYWORDS (3-5 phrases, related search queries):
- Long-tail variations and related queries
- Include condition names, biomarkers, locations, people when relevant
- These become the article's HTML meta keywords

SEO TAGS (4-8 specific, searchable tags):
- Replace generic tags like "mold" or "health" with specific searchable phrases
- Good: "black mold", "mycotoxin exposure", "TGF-beta", "indoor air quality", "CIRS symptoms"
- Bad: "mold", "health", "research", "news"
- Each tag should be a phrase someone might search for

KEYWORD STRATEGY BY CATEGORY:
- Research: biomarker names, health conditions, "mold + [symptom]", study institution
- Regulation: agency name, law/bill name, affected group, "mold regulations [state/country]"
- News: person/company name, location, "mold [event type]", impact
- Industry: company/product, market segment, "mold testing [type]"
- Diagnostics: test name, biomarker, "mold blood test", "mycotoxin urine test"

Return ONLY valid JSON:
{"seoTitle": "optimized title | The Mold Report", "seoDescription": "the meta description", "primaryKeyword": "2-4 word target phrase", "secondaryKeywords": ["phrase 1", "phrase 2", "phrase 3"], "seoTags": ["specific tag 1", "specific tag 2", "specific tag 3", "specific tag 4"]}"""

    prompt = f"""Generate SEO meta title and description for this article:

Title: {article['title']}
Summary: {article['summary'][:400]}
Category: {article['category']}
Tags: {', '.join(article.get('tags', []))}
Source: {article['source']}"""

    result = call_claude(system, prompt, max_tokens=300, model=MODEL_FAST)
    if result:
        try:
            json_match = re.search(r'\{.*\}', strip_json_fences(result), re.DOTALL)
            if json_match:
                parsed = json.loads(json_match.group())
                seo_title = parsed.get('seoTitle', '').strip()
                seo_desc = parsed.get('seoDescription', '').strip()

                # Validate lengths — fall back to defaults if too long
                if seo_title and len(seo_title) <= 70:
                    article['seoTitle'] = seo_title
                else:
                    # Fallback: truncated article title + brand
                    article['seoTitle'] = article['title'][:45] + ' | The Mold Report'
                    print(f"    ⚠ Title too long, using fallback")

                if seo_desc and len(seo_desc) <= 170:
                    article['seoDescription'] = seo_desc
                else:
                    # Fallback: first 155 chars of summary
                    article['seoDescription'] = article['summary'][:155].rsplit(' ', 1)[0] + '...'
                    print(f"    ⚠ Description too long, using fallback")


                # Save keyword data
                primary_kw = parsed.get('primaryKeyword', '').strip()
                if primary_kw:
                    article['primaryKeyword'] = primary_kw

                secondary_kws = parsed.get('secondaryKeywords', [])
                if secondary_kws and isinstance(secondary_kws, list):
                    article['secondaryKeywords'] = secondary_kws[:5]

                seo_tags = parsed.get('seoTags', [])
                if seo_tags and isinstance(seo_tags, list):
                    article['tags'] = seo_tags[:8]

                print(f"    → Title: {article['seoTitle'][:55]}...")
                print(f"    → KW: {article.get('primaryKeyword', 'none')}")
                print(f"    → Tags: {article.get('tags', [])}")
        except (json.JSONDecodeError, AttributeError):
            print("    ⚠ Could not parse SEO response — using defaults")

    # Always ensure fallback values exist
    if 'seoTitle' not in article:
        article['seoTitle'] = article['title'][:45] + ' | The Mold Report'
    if 'seoDescription' not in article:
        article['seoDescription'] = article['summary'][:155].rsplit(' ', 1)[0] + '...'
    if 'primaryKeyword' not in article:
        article['primaryKeyword'] = 'mold exposure'

    return article


# =========================================
# CLASSIFIER
# =========================================
def classify_article(title, summary):
    """Auto-classify article category."""
    text = (title + " " + summary).lower()
    categories = {
        "research": ["study", "research", "findings", "journal", "scientists",
                      "clinical", "trial", "published", "university", "data shows"],
        "regulation": ["law", "regulation", "epa", "legislation", "bill", "compliance",
                        "policy", "government", "federal", "mandate", "guideline"],
        "diagnostics": ["test", "biomarker", "blood test", "diagnostic", "screening",
                         "panel", "lab", "mycotoxin", "assay", "TGF", "MMP", "MSH"],
        "industry": ["company", "startup", "funding", "market", "business", "product",
                      "launch", "investment", "revenue", "technology", "acquisition"],
    }
    scores = {cat: sum(1 for kw in kws if kw in text) for cat, kws in categories.items()}
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else "news"


def extract_tags(text):
    """Extract relevant tags."""
    keywords = [
        "EPA", "WHO", "CDC", "mold", "mycotoxin", "remediation",
        "air quality", "biomarker", "Stachybotrys", "Aspergillus",
        "CIRS", "brain fog", "inflammation", "housing", "school",
        "insurance", "lawsuit", "testing", "treatment", "health",
        "Shoemaker", "TGF", "MMP-9", "MSH", "flooding", "water damage",
    ]
    text_lower = text.lower()
    return [t for t in keywords if t.lower() in text_lower][:6]


def gen_id(title):
    return hashlib.md5(title.lower().strip().encode()).hexdigest()[:12]


# =========================================
# DATA LAYER (articles.json IS the database)
# =========================================
def load_articles():
    if ARTICLES_FILE.exists():
        with open(ARTICLES_FILE) as f:
            return json.load(f)
    return {"lastUpdated": datetime.now(timezone.utc).isoformat(), "articles": []}


def save_articles(data):
    data["lastUpdated"] = datetime.now(timezone.utc).isoformat()
    fixed_count = 0
    for a in data["articles"]:
        # Clean internal pipeline flags before saving
        for key in list(a.keys()):
            if key.startswith('_'):
                del a[key]
        # Validate every image URL before it hits disk (last line of defense)
        img = a.get("imageUrl", "")
        validated = validate_image_url(img, a.get("category", "default"))
        if validated != img:
            a["imageUrl"] = validated
            fixed_count += 1
    if fixed_count:
        print(f"  ⚠ Fixed {fixed_count} invalid image URLs during save")
    with open(ARTICLES_FILE, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(f"\n✓ Saved {len(data['articles'])} articles to articles.json")


def rebuild_embedded(data):
    """Re-embed articles into index.html for local file:// access.

    Uses ``json.JSONDecoder.raw_decode`` to find the end of the embedded JSON
    object regardless of whether it's followed by ``;</script>`` or by more JS in
    the same script block. The previous naive brace-counting approach could be
    fooled by ``{`` / ``}`` inside string literals; ``raw_decode`` is the
    correct parser-aware way to find the object boundary.
    """
    if not INDEX_FILE.exists():
        return
    with open(INDEX_FILE) as f:
        html = f.read()

    marker = 'const EMBEDDED_ARTICLES = '
    if marker not in html:
        print("  ⚠ No EMBEDDED_ARTICLES marker in index.html")
        return

    start = html.index(marker) + len(marker)
    decoder = json.JSONDecoder()
    try:
        # raw_decode returns (object, end_index_in_string). end is already absolute.
        _, end = decoder.raw_decode(html, start)
    except json.JSONDecodeError as exc:
        print(f"  ⚠ Couldn't parse embedded JSON at offset {start}: {exc}")
        return

    minified = json.dumps(data, separators=(',', ':'), ensure_ascii=False)
    new_html = html[:start] + minified + html[end:]

    # Round-trip check: parse what we just wrote, confirm count matches.
    try:
        parsed, _ = decoder.raw_decode(new_html, start)
        assert len(parsed.get('articles', [])) == len(data.get('articles', []))
    except Exception as exc:
        print(f"  ⚠ Embedded JSON failed round-trip ({exc}) — refusing to write")
        return

    with open(INDEX_FILE, 'w') as f:
        f.write(new_html)
    print(f"  ✓ Re-embedded {len(data['articles'])} articles into index.html")


def generate_article_pages(data):
    """Generate full standalone HTML article pages for SEO indexing.
    Each page at /a/{id}.html is a complete, indexable article page with
    full content, structured data, and a link back to the main site."""
    import html as html_module
    articles_dir = SCRIPT_DIR / "a"
    articles_dir.mkdir(exist_ok=True)

    count = 0
    for a in data.get("articles", []):
        if a.get("status") != "published":
            continue

        aid = a["id"]
        title_raw = a["title"]
        title = html_module.escape(title_raw)
        seo_title = html_module.escape(a.get("seoTitle", title_raw + " | The Mold Report"))
        seo_desc = html_module.escape(a.get("seoDescription", a["summary"][:155])).replace("\n", " ")
        summary_raw = a["summary"]
        # Convert summary paragraphs to HTML paragraphs
        summary_paras = [f"<p>{html_module.escape(p.strip())}</p>" for p in summary_raw.split("\n\n") if p.strip()]
        if not summary_paras:
            summary_paras = [f"<p>{html_module.escape(summary_raw)}</p>"]
        article_body_html = "\n    ".join(summary_paras)

        img = a.get("imageUrl", "")
        img_alt = html_module.escape(a.get("imageAlt", title_raw))
        source = html_module.escape(a.get("source", "The Mold Report"))
        source_url = a.get("sourceUrl", "")
        pub_date = a.get("publishedAt", "")
        pub_display = pub_date[:10] if pub_date else ""
        category = html_module.escape(a.get("category", "news"))
        tags = a.get("tags", [])
        tags_str = ", ".join(tags)
        tags_escaped = html_module.escape(tags_str)
        read_time = a.get("readTime", "3 min read")
        word_count = len(summary_raw.split())
        editors_note = a.get("editorsNote", "")
        primary_kw = html_module.escape(a.get("primaryKeyword", tags[0] if tags else "mold"))

        # JSON-LD articleBody (plain text, escaped for JSON)
        article_body_text = summary_raw.replace('"', '\\"').replace("\n", " ")

        # Editor's note HTML
        editors_note_html = ""
        if editors_note and len(editors_note.strip()) > 10:
            editors_note_html = f'<aside class="editors-note"><strong>Editor\'s Note:</strong> {html_module.escape(editors_note)}</aside>'

        # Hero image HTML
        hero_html = ""
        if img:
            hero_html = f'<img class="hero" src="{img}" alt="{img_alt}" loading="eager">'

        # Source link
        source_html = f'<a href="{source_url}" rel="noopener" target="_blank">{source}</a>' if source_url else source

        page_html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{seo_title}</title>
  <meta name="description" content="{seo_desc}">
  <meta name="keywords" content="{tags_escaped}">
  <meta name="robots" content="index, follow, max-snippet:-1, max-image-preview:large">
  <meta name="article:primary_keyword" content="{primary_kw}">
  <link rel="canonical" href="https://themoldreport.org/a/{aid}.html">

  <!-- Open Graph -->
  <meta property="og:type" content="article">
  <meta property="og:title" content="{title}">
  <meta property="og:description" content="{seo_desc}">
  <meta property="og:site_name" content="The Mold Report">
  <meta property="og:url" content="https://themoldreport.org/a/{aid}.html">
  <meta property="og:locale" content="en_US">
  {f'<meta property="og:image" content="{img}">' if img else ''}
  {f'<meta property="og:image:alt" content="{img_alt}">' if img else ''}
  <meta property="article:published_time" content="{pub_date}">
  <meta property="article:section" content="{category}">
  {"".join(f'<meta property="article:tag" content="{html_module.escape(t)}">' for t in tags)}

  <!-- Twitter Card -->
  <meta name="twitter:card" content="summary_large_image">
  <meta name="twitter:title" content="{title}">
  <meta name="twitter:description" content="{seo_desc}">
  {f'<meta name="twitter:image" content="{img}">' if img else ''}

  <!-- JSON-LD Structured Data -->
  <script type="application/ld+json">
  {{
    "@context": "https://schema.org",
    "@type": "NewsArticle",
    "headline": "{title}",
    "description": "{seo_desc}",
    "image": "{img}",
    "datePublished": "{pub_date}",
    "wordCount": {word_count},
    "articleBody": "{article_body_text[:500]}",
    "author": {{
      "@type": "Organization",
      "name": "The Mold Report",
      "url": "https://themoldreport.org"
    }},
    "publisher": {{
      "@type": "Organization",
      "name": "The Mold Report",
      "url": "https://themoldreport.org",
      "logo": {{
        "@type": "ImageObject",
        "url": "https://themoldreport.org/logo.png"
      }}
    }},
    "mainEntityOfPage": {{
      "@type": "WebPage",
      "@id": "https://themoldreport.org/a/{aid}.html"
    }},
    "articleSection": "{category}",
    "keywords": "{tags_escaped}",
    "isAccessibleForFree": true
  }}
  </script>

  <style>
    *,*::before,*::after{{box-sizing:border-box}}
    body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;max-width:720px;margin:0 auto;padding:20px;color:#1a1a1a;line-height:1.7;background:#fafaf8}}
    .site-header{{padding:16px 0;border-bottom:2px solid #1B4D3E;margin-bottom:32px;display:flex;align-items:center;justify-content:space-between}}
    .site-header a{{color:#1B4D3E;text-decoration:none;font-weight:700;font-size:18px}}
    .site-header .home{{font-size:14px;color:#666}}
    .category-badge{{display:inline-block;background:#1B4D3E;color:#fff;padding:3px 10px;border-radius:3px;font-size:12px;text-transform:uppercase;letter-spacing:.5px;margin-bottom:12px}}
    h1{{font-size:32px;line-height:1.2;margin:0 0 16px;color:#111}}
    .article-meta{{color:#666;font-size:14px;margin-bottom:24px;display:flex;flex-wrap:wrap;gap:8px 16px}}
    .article-meta a{{color:#1B4D3E}}
    .hero{{width:100%;max-height:400px;object-fit:cover;border-radius:8px;margin-bottom:24px}}
    .article-body p{{font-size:17px;line-height:1.8;color:#333;margin-bottom:16px}}
    .editors-note{{background:#f0f7f4;border-left:3px solid #1B4D3E;padding:12px 16px;margin:24px 0;font-size:15px;color:#444;border-radius:0 6px 6px 0}}
    .tags{{margin-top:32px;padding-top:16px;border-top:1px solid #e0e0e0}}
    .tags span{{display:inline-block;background:#f0f0f0;padding:4px 10px;border-radius:4px;font-size:13px;margin:4px 4px 4px 0;color:#555}}
    .source-link{{margin-top:24px;padding:16px;background:#f8f8f6;border-radius:8px;font-size:15px}}
    .source-link a{{color:#1B4D3E;font-weight:600}}
    .site-footer{{margin-top:48px;padding:24px 0;border-top:2px solid #1B4D3E;font-size:13px;color:#888;text-align:center}}
    .site-footer a{{color:#1B4D3E}}
    @media(max-width:600px){{h1{{font-size:24px}}body{{padding:16px}}.article-body p{{font-size:16px}}}}
  </style>
</head>
<body>
  <header class="site-header">
    <a href="https://themoldreport.org">The Mold Report</a>
    <a class="home" href="https://themoldreport.org">&#8592; All Articles</a>
  </header>

  <article>
    <span class="category-badge">{category}</span>
    <h1>{title}</h1>
    <div class="article-meta">
      <span>Source: {source_html}</span>
      <span>{pub_display}</span>
      <span>{read_time}</span>
    </div>

    {hero_html}

    <div class="article-body">
      {article_body_html}
    </div>

    {editors_note_html}

    <div class="source-link">
      &#128279; <a href="{source_url}" target="_blank" rel="noopener">Read original source</a>
    </div>

    <div class="tags">
      {"".join(f'<span>{html_module.escape(t)}</span>' for t in tags)}
    </div>
  </article>

  <footer class="site-footer">
    <p><a href="https://themoldreport.org">The Mold Report</a> &mdash; AI-curated mold &amp; indoor health news</p>
    <p>&copy; 2026 The Mold Report. All rights reserved.</p>
  </footer>
</body>
</html>"""

        page_path = articles_dir / f"{aid}.html"
        with open(page_path, 'w') as f:
            f.write(page_html)
        count += 1

    print(f"  ✓ Generated {count} standalone article pages in /a/")



# =========================================
# RSS FETCH
# =========================================
def fetch_rss():
    if not feedparser:
        print("⚠ feedparser not installed. Run: pip install feedparser")
        return []

    if not RSS_FEEDS:
        print("⚠ No RSS feeds configured. Set MOLD_REPORT_RSS_1, _2, etc. in .env")
        return []

    articles = []
    seen_titles = set()
    for feed_url in RSS_FEEDS:
        print(f"→ Fetching: {feed_url[:70]}...")
        feed = feedparser.parse(feed_url)
        print(f"  Found {len(feed.entries)} entries")
        for entry in feed.entries:
            title = entry.get("title", "")
            if BeautifulSoup:
                title = BeautifulSoup(title, "html.parser").get_text(strip=True)

            title_key = title.lower().strip()
            if title_key in seen_titles:
                continue
            seen_titles.add(title_key)

            summary = entry.get("summary", entry.get("description", ""))
            if BeautifulSoup:
                summary = BeautifulSoup(summary, "html.parser").get_text(strip=True)
            link = entry.get("link", "")

            # Google Alerts wraps real URLs in redirects: extract the actual source
            if 'google.com/url' in link:
                parsed_qs = parse_qs(urlparse(link).query)
                real_url = parsed_qs.get('url', parsed_qs.get('q', ['']))[0]
                if real_url:
                    link = real_url

            pub_date = datetime.now(timezone.utc).isoformat()
            if entry.get("published"):
                try:
                    from email.utils import parsedate_to_datetime
                    pub_date = parsedate_to_datetime(entry["published"]).isoformat()
                except Exception:
                    pass

            domain = urlparse(link).netloc.replace("www.", "") if link else "Unknown"
            # Use full domain for source name (e.g. "canberratimes.com.au") not truncated
            source_name = domain if domain != "Unknown" else "Unknown"

            articles.append({
                "id": gen_id(title),
                "title": title,
                "summary": summary,
                "source": source_name,
                "sourceUrl": link,
                "author": source_name,
                "publishedAt": pub_date,
                "category": classify_article(title, summary),
                "imageUrl": "",
                "imageAlt": "",
                "status": "draft",
                "qcReviewer": "",
                "qcTimestamp": "",
                "tags": extract_tags(title + " " + summary),
                "featured": False,
                "readTime": 3,
            })

    print(f"→ Fetched {len(articles)} articles from {len(RSS_FEEDS)} feeds (deduped)")
    return articles


# =========================================
# PUBMED FETCH (peer-reviewed research via eutils API)
# =========================================
def fetch_pubmed():
    """Fetch recent peer-reviewed research from PubMed using NCBI eutils API."""
    if not requests:
        print("⚠ requests not installed")
        return []

    print(f"\n→ Fetching PubMed research (last {PUBMED_DAYS_BACK} days)...")
    all_ids = set()
    for query in PUBMED_SEARCHES:
        search_url = (
            "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
            f"?db=pubmed&term={query.replace(' ', '+')}"
            f"&retmax={PUBMED_MAX_PER_QUERY}&sort=date"
            f"&datetype=pdat&reldate={PUBMED_DAYS_BACK}&retmode=json"
        )
        try:
            r = requests.get(search_url, timeout=10)
            data = r.json()
            ids = data.get("esearchresult", {}).get("idlist", [])
            all_ids.update(ids)
        except Exception as e:
            print(f"  ⚠ PubMed search error: {e}")

    if not all_ids:
        print("  No new PubMed articles found")
        return []

    print(f"  Found {len(all_ids)} unique PubMed IDs")

    # Fetch summaries for all IDs
    id_str = ",".join(all_ids)
    summary_url = (
        "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"
        f"?db=pubmed&id={id_str}&retmode=json"
    )
    try:
        r = requests.get(summary_url, timeout=15)
        results = r.json().get("result", {})
    except Exception as e:
        print(f"  ⚠ PubMed summary error: {e}")
        return []

    articles = []
    for pid in all_ids:
        info = results.get(pid, {})
        if not info:
            continue

        title = info.get("title", "").strip()
        if not title:
            continue

        journal = info.get("source", "")
        pubdate = info.get("pubdate", "")
        authors = info.get("authors", [])
        author_str = authors[0].get("name", "") if authors else ""

        # Build summary from available fields
        summary = f"Published in {journal}. " if journal else ""
        if author_str:
            summary += f"Lead author: {author_str}. "
        summary += title

        # Parse date
        pub_iso = datetime.now(timezone.utc).isoformat()
        if pubdate:
            try:
                # PubMed dates can be "2026 Apr 11" or "2026 Mar" or "2026"
                for fmt in ["%Y %b %d", "%Y %b", "%Y"]:
                    try:
                        dt = datetime.strptime(pubdate, fmt)
                        pub_iso = dt.replace(tzinfo=timezone.utc).isoformat()
                        break
                    except ValueError:
                        continue
            except Exception:
                pass

        source_url = f"https://pubmed.ncbi.nlm.nih.gov/{pid}/"

        articles.append({
            "id": gen_id(title),
            "title": title,
            "summary": summary,
            "source": journal or "PubMed",
            "sourceUrl": source_url,
            "author": author_str or journal or "PubMed",
            "publishedAt": pub_iso,
            "category": "research",  # PubMed = always research
            "imageUrl": "",
            "imageAlt": "",
            "status": "draft",
            "qcReviewer": "",
            "qcTimestamp": "",
            "tags": extract_tags(title + " " + summary),
            "featured": False,
            "readTime": 3,
        })

    print(f"→ Fetched {len(articles)} PubMed articles")
    return articles


# =========================================
# GOVERNMENT RSS FEEDS (EPA, CDC, HUD, NIH, NIOSH)
# =========================================
def fetch_gov_rss():
    """Fetch government news feeds, filtered to mold-relevant articles only."""
    if not feedparser:
        print("⚠ feedparser not installed")
        return []

    print(f"\n→ Fetching government/institutional feeds...")
    articles = []
    seen_titles = set()

    for feed_url in GOV_RSS_FEEDS:
        try:
            feed = feedparser.parse(feed_url)
            source_name = feed_url.split("/")[2].replace("www.", "").split(".")[0].upper()
            relevant = 0

            for entry in feed.entries:
                title = entry.get("title", "")
                summary = entry.get("summary", entry.get("description", ""))

                if BeautifulSoup:
                    title = BeautifulSoup(title, "html.parser").get_text(strip=True)
                    summary = BeautifulSoup(summary, "html.parser").get_text(strip=True)

                # Filter: only keep mold-relevant articles
                combined = (title + " " + summary).lower()
                if not any(kw in combined for kw in GOV_FILTER_KEYWORDS):
                    continue

                title_key = title.lower().strip()
                if title_key in seen_titles:
                    continue
                seen_titles.add(title_key)

                link = entry.get("link", "")

                pub_date = datetime.now(timezone.utc).isoformat()
                if entry.get("published"):
                    try:
                        from email.utils import parsedate_to_datetime
                        pub_date = parsedate_to_datetime(entry["published"]).isoformat()
                    except Exception:
                        pass

                domain = urlparse(link).netloc.replace("www.", "") if link else source_name

                articles.append({
                    "id": gen_id(title),
                    "title": title,
                    "summary": summary[:500],
                    "source": source_name,
                    "sourceUrl": link,
                    "author": source_name,
                    "publishedAt": pub_date,
                    "category": classify_article(title, summary),
                    "imageUrl": "",
                    "imageAlt": "",
                    "status": "draft",
                    "qcReviewer": "",
                    "qcTimestamp": "",
                    "tags": extract_tags(title + " " + summary),
                    "featured": False,
                    "readTime": 3,
                })
                relevant += 1

            print(f"  {source_name}: {relevant} relevant articles (from {len(feed.entries)} total)")
        except Exception as e:
            print(f"  ⚠ Error fetching {feed_url[:50]}: {e}")

    print(f"→ Fetched {len(articles)} government/institutional articles")
    return articles


# =========================================
# READER TIP INGESTION (via Formspree API)
# =========================================
TIPS_FILE = SCRIPT_DIR / "tips.json"
FORMSPREE_FORM_ID = "mwvalpzg"

def fetch_tips():
    """Fetch reader-submitted tips directly from Formspree API.
    Requires FORMSPREE_API_KEY env var (Personal plan, $10/mo).
    Tracks processed submission IDs in tips.json to avoid re-processing.
    Each tip is converted to an article dict with _source_type='reader_tip'
    and run through The Bouncer (tip_screening_agent) downstream."""

    api_key = os.environ.get("FORMSPREE_API_KEY", "")
    if not api_key:
        print("→ No FORMSPREE_API_KEY set — skipping tip fetch")
        # Fall back to manual tips.json if someone added tips by hand
        return _fetch_tips_from_file()
    if not requests:
        print("→ requests not installed — skipping tip fetch")
        return []

    # Load already-processed submission IDs
    processed_ids = set()
    if TIPS_FILE.exists():
        try:
            with open(TIPS_FILE) as f:
                tips_data = json.load(f)
            processed_ids = set(tips_data.get("processed_ids", []))
        except (json.JSONDecodeError, KeyError):
            tips_data = {"processed_ids": []}
    else:
        tips_data = {"processed_ids": []}

    # Fetch submissions from Formspree API
    print("\n→ Fetching tips from Formspree API...")
    try:
        resp = requests.get(
            f"https://formspree.io/api/0/forms/{FORMSPREE_FORM_ID}/submissions",
            headers={"Authorization": f"Bearer {api_key}"},
            params={"limit": 50},
            timeout=30,
        )
        if resp.status_code == 401:
            print("  ✗ Formspree API key invalid or expired")
            return _fetch_tips_from_file()
        if resp.status_code == 403:
            print("  ✗ Formspree API access requires Personal plan ($10/mo)")
            return _fetch_tips_from_file()
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"  ✗ Formspree API error: {e}")
        return _fetch_tips_from_file()

    # Formspree returns {"submissions": [...]} — each has an "_id" field
    submissions = data.get("submissions", [])
    if not submissions:
        print("→ No submissions in Formspree")
        return []

    # Filter to only new submissions
    new_subs = [s for s in submissions if s.get("_id") not in processed_ids]
    if not new_subs:
        print("→ No new tips (all already processed)")
        return []

    print(f"→ Found {len(new_subs)} new tip(s) from Formspree")
    articles = []
    newly_processed = []

    for sub in new_subs:
        sub_id = sub.get("_id", "")
        title = sub.get("title", "").strip()
        if not title:
            # No title = not a real tip, skip but mark processed
            newly_processed.append(sub_id)
            continue

        articles.append({
            "id": gen_id(title),
            "title": title,
            "summary": sub.get("summary", ""),
            "source": sub.get("name", "Reader Tip"),
            "sourceUrl": sub.get("url", ""),
            "author": sub.get("name", "Anonymous"),
            "publishedAt": sub.get("_date", datetime.now(timezone.utc).isoformat()),
            "category": sub.get("category", "news"),
            "imageUrl": "",
            "imageAlt": "",
            "status": "draft",
            "qcReviewer": "",
            "qcTimestamp": "",
            "tags": extract_tags(title + " " + sub.get("summary", "")),
            "featured": False,
            "readTime": 3,
            "_source_type": "reader_tip",
            "_submitter_name": sub.get("name", "Anonymous"),
            "_submitter_email": sub.get("email", ""),
            "_tip_id": sub_id,
        })
        newly_processed.append(sub_id)

    # Save updated processed IDs so we don't re-fetch these
    tips_data["processed_ids"] = list(processed_ids | set(newly_processed))
    with open(TIPS_FILE, "w") as f:
        json.dump(tips_data, f, indent=2, ensure_ascii=False)

    print(f"→ Loaded {len(articles)} tips for pipeline review (The Bouncer will screen them)")
    return articles


def _fetch_tips_from_file():
    """Fallback: load tips from tips.json if Formspree API is unavailable.
    This lets someone manually add tips to the file if needed."""
    if not TIPS_FILE.exists():
        return []
    try:
        with open(TIPS_FILE) as f:
            tips_data = json.load(f)
    except (json.JSONDecodeError, KeyError):
        return []

    pending = [t for t in tips_data.get("tips", []) if t.get("status") == "pending"]
    if not pending:
        return []

    print(f"\n→ Processing {len(pending)} manually-added tips from tips.json...")
    articles = []
    for tip in pending:
        title = tip.get("title", "").strip()
        if not title:
            continue
        articles.append({
            "id": gen_id(title),
            "title": title,
            "summary": tip.get("summary", ""),
            "source": tip.get("name", "Reader Tip"),
            "sourceUrl": tip.get("url", ""),
            "author": tip.get("name", "Anonymous"),
            "publishedAt": tip.get("submittedAt", datetime.now(timezone.utc).isoformat()),
            "category": tip.get("category", "news"),
            "imageUrl": "",
            "imageAlt": "",
            "status": "draft",
            "qcReviewer": "",
            "qcTimestamp": "",
            "tags": extract_tags(title + " " + tip.get("summary", "")),
            "featured": False,
            "readTime": 3,
            "_source_type": "reader_tip",
            "_submitter_name": tip.get("name", "Anonymous"),
            "_submitter_email": tip.get("email", ""),
            "_tip_id": tip.get("id", ""),
        })
        tip["status"] = "processed"

    with open(TIPS_FILE, "w") as f:
        json.dump(tips_data, f, indent=2, ensure_ascii=False)
    return articles


# =========================================
# MAIN PIPELINE: Fully automated
# =========================================
def run_pipeline(min_score=DEFAULT_MIN_SCORE, dry_run=False):
    """
    The full auto-publish pipeline. No human step.

    1. Fetch RSS feeds
    2. Dedup against existing articles
    3. Run each article through all gates
    4. Auto-publish anything scoring >= min_score that passes all checks
    5. Re-embed into index.html
    """
    print("=" * 60)
    print("  THE MOLD REPORT — Auto-Publish Pipeline")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M')} | Min score: {min_score}")
    print("=" * 60)

    # Load existing articles
    data = load_articles()
    existing_ids = {a["id"] for a in data["articles"]}
    print(f"\n→ {len(existing_ids)} articles currently published")

    # Fetch from all sources
    raw = []
    raw.extend(fetch_rss())        # Google Alerts RSS
    raw.extend(fetch_pubmed())     # PubMed peer-reviewed research
    raw.extend(fetch_gov_rss())    # EPA, CDC, HUD, NIH, NIOSH
    raw.extend(fetch_tips())       # Reader-submitted tips

    # Dedup across all sources
    seen = set()
    deduped = []
    for a in raw:
        if a["id"] not in existing_ids and a["id"] not in seen:
            seen.add(a["id"])
            deduped.append(a)
    fresh = deduped
    print(f"\n→ {len(fresh)} new articles after dedup (from {len(raw)} raw)\n")

    # Load rejection cache (expires entries older than 30 days)
    _reject_cache_file = SCRIPT_DIR / ".rejected_cache.json"
    _reject_cache = {}
    if _reject_cache_file.exists():
        try:
            _reject_cache = json.load(open(_reject_cache_file))
            if isinstance(_reject_cache, list):
                # Migrate old format (list of IDs) to new format (dict with timestamps)
                _reject_cache = {rid: datetime.now(timezone.utc).isoformat() for rid in _reject_cache}
            # Expire entries older than 30 days
            cutoff = (datetime.now(timezone.utc) - __import__('datetime').timedelta(days=30)).isoformat()
            _reject_cache = {k: v for k, v in _reject_cache.items() if v > cutoff}
        except: pass
    _rejected_ids = set(_reject_cache.keys())
    fresh = [a for a in fresh if a["id"] not in _rejected_ids]
    print(f"→ {len(_rejected_ids)} cached rejections skipped")

    if not fresh:
        print("✓ Nothing new. Site is up to date.")
        return

    published = []
    rejected = []
    import time as _time
    _pipeline_start = _time.monotonic()

    for article in fresh:
        # Check timeout before starting a new article
        elapsed = _time.monotonic() - _pipeline_start
        if elapsed > PIPELINE_TIMEOUT_SECONDS:
            print(f"\n⏱ Pipeline timeout ({int(elapsed)}s > {PIPELINE_TIMEOUT_SECONDS}s). "
                  f"Stopping with {len(published)} published, {len(rejected)} rejected.")
            print(f"  Remaining {len(fresh) - len(published) - len(rejected)} articles deferred to next run.")
            break

        print(f"\n{'─' * 56}")
        print(f"  {article['title'][:65]}")
        print(f"  {article['source']} | {article['category']}")
        print(f"{'─' * 56}")

        # Gate 0: Duplicate detection (content-aware)
        all_existing = data["articles"] + published  # check against published + already-accepted this run
        article = duplicate_detection_agent(article, all_existing)
        if not article.get('_dedup_pass', True):
            print(f"  ✗ REJECTED: duplicate of {article.get('_duplicate_of', '?')}")
            rejected.append(("duplicate", article))
            continue

        # Gate 1: Freshness
        if not freshness_gate(article):
            rejected.append(("freshness", article))
            continue

        # Gate 2: Source verification (reader tips without URLs skip this gate)
        is_tip = article.get('_source_type') == 'reader_tip'
        if not is_tip and not source_verification_gate(article):
            rejected.append(("source", article))
            continue

        # Gate 2b: Tip screening (reader tips only — checks editorial validity + alignment)
        if is_tip:
            article = tip_screening_agent(article)
            if not article.get('_tip_approved', False):
                print(f"  ✗ REJECTED: Tip failed editorial screening")
                rejected.append(("tip_screening", article))
                continue

        # Gate 3: Interest scoring (auto-gate)
        article = interest_agent(article)
        score = article.get('_interest_score', 5)
        if score < min_score:
            print(f"  ✗ SKIPPED: interest {score}/10 (need {min_score}+)")
            rejected.append(("interest", article))
            continue

        # Passed the interest bar — run through full editorial pipeline
        print(f"  ✓ INTERESTING ({score}/10) — running full pipeline...")

        # Gate 4: Headline rewrite (make titles compelling)
        article = headline_agent(article)

        # Gate 5: Editorial rewrite
        article = editorial_agent(article)

        # Gate 6: Compliance check (auto-corrects language)
        article = compliance_agent(article)

        # Gate 7: Shoemaker Research Lens (MOST IMPORTANT GATE — kills off-topic content)
        article = research_agent(article)
        if not article.get('_research_verified', True):
            print(f"  ✗ REJECTED: Failed Shoemaker alignment")
            rejected.append(("shoemaker_lens", article))
            continue


        # Gate 9: Photo assignment
        article = photo_agent(article)

        # Gate 10: SEO optimization (meta title + description)
        article = seo_agent(article)

        # Final check: all gates passed?
        compliance_ok = article.get('_compliance_pass', True)
        research_ok = article.get('_research_verified', True)

        if compliance_ok and research_ok:
            article['status'] = 'published'
            article['qcReviewer'] = 'AI Editorial Pipeline v1.4'
            article['qcTimestamp'] = datetime.now(timezone.utc).isoformat()
            published.append(article)
            print(f"  ✓ PUBLISHED (score: {score}/10)")
            # INCREMENTAL SAVE: persist after each article so timeout doesn't lose work
            if not dry_run:
                _inc_data = load_articles()
                _inc_data["articles"] = published + [a for a in _inc_data["articles"] if a["id"] not in {p["id"] for p in published}]
                _inc_data["articles"].sort(key=lambda a: a.get("publishedAt", ""), reverse=True)
                for _a in _inc_data["articles"]: _a["featured"] = False
                for _a in _inc_data["articles"][:2]: _a["featured"] = True
                save_articles(_inc_data)
                rebuild_embedded(_inc_data)
                generate_article_pages(_inc_data)
                print(f"  ✓ Incremental save ({len(_inc_data['articles'])} articles)")
        else:
            reasons = []
            if not compliance_ok: reasons.append("compliance")
            if not research_ok: reasons.append("research")
            print(f"  ✗ REJECTED ({', '.join(reasons)} failed)")
            rejected.append(("pipeline", article))

        # Cap articles per run
        if len(published) >= MAX_ARTICLES_PER_RUN:
            print(f"\n→ Hit max {MAX_ARTICLES_PER_RUN} articles per run. Stopping.")
            break

    # Final save: reload from disk (incremental saves already persisted each article)
    # Just ensure featured flags and trimming are correct
    if published and not dry_run:
        data = load_articles()
        data["articles"].sort(key=lambda a: a.get("publishedAt", ""), reverse=True)
        for a in data["articles"]:
            a["featured"] = False
        for a in data["articles"][:2]:
            a["featured"] = True
        if len(data["articles"]) > MAX_TOTAL_ARTICLES:
            trimmed = len(data["articles"]) - MAX_TOTAL_ARTICLES
            data["articles"] = data["articles"][:MAX_TOTAL_ARTICLES]
            print(f"  ✂ Trimmed {trimmed} oldest articles (max {MAX_TOTAL_ARTICLES})")
        save_articles(data)
        rebuild_embedded(data)
        generate_article_pages(data)

    # Save rejection cache (with timestamps for expiry)
    now = datetime.now(timezone.utc).isoformat()
    for reason, art in rejected:
        _reject_cache[art["id"]] = now
    with open(_reject_cache_file, "w") as _f:
        json.dump(_reject_cache, _f)

    # Summary
    print(f"\n{'=' * 60}")
    print(f"  RESULTS")
    print(f"  Published: {len(published)}")
    print(f"  Rejected:  {len(rejected)}")
    if rejected:
        reasons = {}
        for reason, _ in rejected:
            reasons[reason] = reasons.get(reason, 0) + 1
        for reason, count in reasons.items():
            print(f"    - {reason}: {count}")
    print(f"  Total on site: {len(data['articles'])}")
    if dry_run:
        print(f"  (DRY RUN — nothing saved)")
    print(f"{'=' * 60}")


# =========================================
# FETCH-ONLY MODE (no API key needed)
# =========================================
def fetch_only_pipeline():
    """
    Fetch-only mode: gather candidates WITHOUT needing an API key.

    Runs: RSS fetch → PubMed fetch → Gov feeds → Tips → Dedup → Freshness gate →
          Source verification gate → Output candidates.json

    The Claude session then evaluates each candidate (interest, headline, editorial,
    compliance, research alignment, SEO) and writes approved articles to approved.json.
    Finally, --publish mode takes approved.json and deploys.

    This is the secure pattern: the pipeline script never touches the API key.
    """
    print("=" * 60)
    print("  THE MOLD REPORT — Fetch-Only Mode")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 60)

    # Load existing articles
    data = load_articles()
    existing_ids = {a["id"] for a in data["articles"]}
    existing_articles = data["articles"]
    print(f"\n→ {len(existing_ids)} articles currently published")

    # Fetch from all sources
    raw = []
    raw.extend(fetch_rss())
    raw.extend(fetch_pubmed())
    raw.extend(fetch_gov_rss())
    raw.extend(fetch_tips())

    # Dedup across all sources
    seen = set()
    deduped = []
    for a in raw:
        if a["id"] not in existing_ids and a["id"] not in seen:
            seen.add(a["id"])
            deduped.append(a)
    fresh = deduped
    print(f"\n→ {len(fresh)} new articles after dedup (from {len(raw)} raw)\n")

    # Load rejection cache
    _reject_cache_file = SCRIPT_DIR / ".rejected_cache.json"
    _reject_cache = {}
    if _reject_cache_file.exists():
        try:
            _reject_cache = json.load(open(_reject_cache_file))
            if isinstance(_reject_cache, list):
                _reject_cache = {rid: datetime.now(timezone.utc).isoformat() for rid in _reject_cache}
            cutoff = (datetime.now(timezone.utc) - __import__('datetime').timedelta(days=30)).isoformat()
            _reject_cache = {k: v for k, v in _reject_cache.items() if v > cutoff}
        except: pass
    _rejected_ids = set(_reject_cache.keys())
    fresh = [a for a in fresh if a["id"] not in _rejected_ids]
    print(f"→ {len(_rejected_ids)} cached rejections skipped")

    if not fresh:
        print("✓ Nothing new. Site is up to date.")
        # Write empty candidates file so the caller knows
        candidates_file = SCRIPT_DIR / "candidates.json"
        with open(candidates_file, "w") as f:
            json.dump([], f, indent=2, ensure_ascii=False)
        print(f"✓ Wrote 0 candidates to candidates.json")
        return

    candidates = []
    rejected = []

    for article in fresh:
        print(f"\n{'─' * 56}")
        print(f"  {article['title'][:65]}")
        print(f"  {article['source']} | {article['category']}")
        print(f"{'─' * 56}")

        # Gate 0: Duplicate detection (content-aware, no API)
        all_existing = existing_articles + candidates
        article = duplicate_detection_agent(article, all_existing)
        if not article.get('_dedup_pass', True):
            print(f"  ✗ REJECTED: duplicate of {article.get('_duplicate_of', '?')}")
            rejected.append(("duplicate", article))
            continue

        # Gate 1: Freshness (no API)
        if not freshness_gate(article):
            rejected.append(("freshness", article))
            continue

        # Gate 2: Source verification (no API, tips exempt)
        is_tip = article.get('_source_type') == 'reader_tip'
        if not is_tip and not source_verification_gate(article):
            rejected.append(("source", article))
            continue

        # Passed all non-API gates — this is a candidate
        print(f"  ✓ CANDIDATE (passed dedup + freshness + source)")
        candidates.append(article)

    # Cache rejections
    now = datetime.now(timezone.utc).isoformat()
    for reason, art in rejected:
        _reject_cache[art["id"]] = now
    with open(_reject_cache_file, "w") as _f:
        json.dump(_reject_cache, _f)

    # Write candidates
    candidates_file = SCRIPT_DIR / "candidates.json"
    # Clean internal flags for output (keep raw data for Claude to evaluate)
    output = []
    for c in candidates:
        clean = {k: v for k, v in c.items() if not k.startswith('_')}
        output.append(clean)
    with open(candidates_file, "w") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"\n{'=' * 60}")
    print(f"  FETCH-ONLY RESULTS")
    print(f"  Candidates: {len(candidates)}")
    print(f"  Rejected:   {len(rejected)}")
    if rejected:
        reasons = {}
        for reason, _ in rejected:
            reasons[reason] = reasons.get(reason, 0) + 1
        for reason, count in reasons.items():
            print(f"    - {reason}: {count}")
    print(f"  Output: candidates.json")
    print(f"{'=' * 60}")


# =========================================
# PUBLISH MODE (no API key needed)
# =========================================
def publish_approved(approved_file):
    """
    Publish mode: take pre-scored/approved articles and deploy them.

    Reads approved articles from a JSON file (written by the Claude session),
    runs photo_agent (no API), saves to articles.json, rebuilds index.html,
    generates share pages, and deploys to GitHub.

    Expected input format: list of article objects, each with all required fields
    (id, title, summary, source, sourceUrl, category, publishedAt, imageUrl)
    plus optional pipeline metadata (interest_score, seoTitle, seoDescription).
    """
    print("=" * 60)
    print("  THE MOLD REPORT — Publish Mode")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"  Input: {approved_file}")
    print("=" * 60)

    approved_path = Path(approved_file)
    if not approved_path.exists():
        print(f"⚠ File not found: {approved_file}")
        return

    try:
        with open(approved_path) as f:
            content = f.read().strip()
            if not content:
                print("✓ Empty file — nothing to publish.")
                return
            approved = json.loads(content)
    except (json.JSONDecodeError, ValueError) as e:
        print(f"⚠ Invalid JSON in {approved_file}: {e}")
        return

    if not approved:
        print("✓ No approved articles to publish.")
        return

    print(f"\n→ {len(approved)} approved articles to publish")

    data = load_articles()
    existing_ids = {a["id"] for a in data["articles"]}
    published = []
    blocked = []

    api_key_present = bool(os.environ.get("ANTHROPIC_API_KEY"))
    if not api_key_present:
        print("  ⚠ ANTHROPIC_API_KEY not set — semantic compliance_agent will be skipped.")
        print("    Deterministic compliance_lint still runs (it does not require an API key).")

    for article in approved:
        aid = article.get("id", "?")
        if aid in existing_ids:
            print(f"  ⚠ Skipping {aid} — already published")
            continue

        print(f"\n  Publishing: {article['title'][:65]}")

        # GATE 1: Deterministic compliance lint (always runs, no API).
        # Hard violations BLOCK publishing for this article.
        hard, soft = compliance_lint(article)
        for warn in soft:
            print(f"    ⚠ Soft warning: {warn['name']} (matched: {warn['match']!r})")
        if hard:
            print(f"    ⛔ BLOCKED — {len(hard)} hard compliance violation(s):")
            for v in hard:
                print(f"       • {v['name']} (matched: {v['match']!r})")
                print(f"         → {v['message']}")
            blocked.append({"id": aid, "title": article.get("title", ""), "violations": hard})
            continue

        # GATE 2: Semantic compliance review via Claude (only if API key set).
        if api_key_present:
            article = compliance_agent(article)

        # Run photo agent (no API needed — just OG/Unsplash URL assignment)
        article = photo_agent(article)

        # Set publish metadata
        article['status'] = 'published'
        article['qcReviewer'] = 'AI Editorial Pipeline v1.4 (session-scored)'
        article['qcTimestamp'] = datetime.now(timezone.utc).isoformat()
        if not article.get('publishedAt'):
            article['publishedAt'] = datetime.now(timezone.utc).isoformat()

        published.append(article)
        print(f"  ✓ PUBLISHED")

    if blocked:
        print(f"\n  ⛔ {len(blocked)} article(s) blocked by compliance_lint:")
        for b in blocked:
            print(f"     - {b['title'][:80]}")
            for v in b["violations"]:
                print(f"         {v['name']}")

    if not published:
        print("\n✓ No new articles to publish.")
        if blocked:
            print(f"  ({len(blocked)} blocked by compliance — fix and re-run.)")
        return

    # Merge into existing articles
    data["articles"] = published + data["articles"]
    data["articles"].sort(key=lambda a: a.get("publishedAt", ""), reverse=True)

    # Set featured flags
    for a in data["articles"]:
        a["featured"] = False
    for a in data["articles"][:2]:
        a["featured"] = True

    # Trim if over max
    if len(data["articles"]) > MAX_TOTAL_ARTICLES:
        trimmed = len(data["articles"]) - MAX_TOTAL_ARTICLES
        data["articles"] = data["articles"][:MAX_TOTAL_ARTICLES]
        print(f"  ✂ Trimmed {trimmed} oldest articles (max {MAX_TOTAL_ARTICLES})")

    # Save everything
    save_articles(data)
    rebuild_embedded(data)
    generate_article_pages(data)

    print(f"\n{'=' * 60}")
    print(f"  PUBLISH RESULTS")
    print(f"  New articles published: {len(published)}")
    print(f"  Total on site: {len(data['articles'])}")
    print(f"{'=' * 60}")


# =========================================
# COMPLIANCE AUDIT (standalone)
# =========================================
def compliance_check_existing():
    """Run compliance check on all existing published articles."""
    print("=" * 60)
    print("  Compliance Audit — Existing Articles")
    print("=" * 60)

    data = load_articles()
    articles = data.get("articles", [])
    print(f"\n→ Checking {len(articles)} articles\n")

    issues_found = 0
    for article in articles:
        article = compliance_agent(article)
        if not article.get('_compliance_pass', True):
            issues_found += 1

    save_articles(data)
    rebuild_embedded(data)
    generate_article_pages(data)
    print(f"\n  {issues_found} articles had compliance issues (auto-corrected)")


# =========================================
# SEO BACKFILL (standalone)
# =========================================
def seo_backfill():
    """Generate SEO meta titles and descriptions for all existing articles that don't have them."""
    print("=" * 60)
    print("  SEO Backfill — Generating Meta Tags")
    print("=" * 60)

    data = load_articles()
    articles = data.get("articles", [])

    # Find articles missing SEO fields
    needs_seo = [a for a in articles if not a.get('seoTitle') or not a.get('seoDescription')]
    print(f"\n→ {len(needs_seo)} of {len(articles)} articles need SEO metadata\n")

    if not needs_seo:
        print("✓ All articles already have SEO metadata.")
        return

    updated = 0
    batch_size = 10
    for i, article in enumerate(needs_seo):
        print(f"\n[{i+1}/{len(needs_seo)}]")
        article = seo_agent(article)
        if article.get('seoTitle') and article.get('seoDescription'):
            updated += 1

        # Save progress every batch_size articles
        if (i + 1) % batch_size == 0:
            print(f"\n  💾 Saving batch ({i+1}/{len(needs_seo)})...")
            save_articles(data)

    if updated:
        save_articles(data)
        rebuild_embedded(data)
        generate_article_pages(data)

    print(f"\n{'=' * 60}")
    print(f"  SEO Backfill Complete")
    print(f"  Updated: {updated} articles")
    print(f"{'=' * 60}")


# =========================================
# CLI
# =========================================
def main():
    parser = argparse.ArgumentParser(
        description="The Mold Report — Fully Automated AI Editorial Pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
How it works:
  Run with no arguments for the full auto-publish pipeline (needs API key).

  Scheduled task mode (no API key needed):
  python editorial_pipeline.py --fetch-only        # Step 1: fetch + filter → candidates.json
  # Claude session scores candidates here
  python editorial_pipeline.py --publish approved.json  # Step 2: publish + deploy

  Other modes:
  python editorial_pipeline.py              # Full auto-publish (needs ANTHROPIC_API_KEY)
  python editorial_pipeline.py --dry-run    # Process but don't save
  python editorial_pipeline.py --min-score 6  # Lower the bar
  python editorial_pipeline.py --compliance-check  # Audit existing articles
""")
    parser.add_argument("--min-score", type=int, default=DEFAULT_MIN_SCORE,
                        help=f"Minimum interest score to auto-publish (default: {DEFAULT_MIN_SCORE})")
    parser.add_argument("--dry-run", action="store_true",
                        help="Process but don't save anything")
    parser.add_argument("--compliance-check", action="store_true",
                        help="Run compliance audit on existing articles")
    parser.add_argument("--seo-backfill", action="store_true",
                        help="Generate SEO meta titles/descriptions for existing articles")
    parser.add_argument("--deploy", action="store_true",
                        help="Push to GitHub Pages after pipeline runs")
    parser.add_argument("--fetch-only", action="store_true",
                        help="Fetch and filter candidates only (no API key needed). Outputs candidates.json")
    parser.add_argument("--publish", type=str, metavar="FILE",
                        help="Publish pre-approved articles from FILE (no API key needed). Deploys to GitHub.")
    args = parser.parse_args()

    if args.fetch_only:
        fetch_only_pipeline()
    elif args.publish:
        publish_approved(args.publish)
        # Auto-deploy after publishing
        has_credentials = bool(os.environ.get("GITHUB_TOKEN")) and bool(os.environ.get("GITHUB_REPO_URL"))
        if args.deploy or has_credentials:
            deploy_to_github()
    elif args.seo_backfill:
        seo_backfill()
    elif args.compliance_check:
        compliance_check_existing()
    else:
        run_pipeline(min_score=args.min_score, dry_run=args.dry_run)
        # Auto-deploy to GitHub Pages
        has_credentials = bool(os.environ.get("GITHUB_TOKEN")) and bool(os.environ.get("GITHUB_REPO_URL"))
        if not args.dry_run and (args.deploy or has_credentials):
            deploy_to_github()


def generate_sitemap(data):
    """Generate sitemap.xml for search engine discovery."""
    from datetime import datetime
    site_url = "https://themoldreport.org"
    
    urls = []
    # Homepage — highest priority
    urls.append(f"""  <url>
    <loc>{site_url}/</loc>
    <changefreq>daily</changefreq>
    <priority>1.0</priority>
  </url>""")
    
    # About page
    urls.append(f"""  <url>
    <loc>{site_url}/about.html</loc>
    <changefreq>monthly</changefreq>
    <priority>0.6</priority>
  </url>""")
    
    # Mold 101
    urls.append(f"""  <url>
    <loc>{site_url}/mold-101.html</loc>
    <changefreq>monthly</changefreq>
    <priority>0.7</priority>
  </url>""")

    # Category page
    urls.append(f"""  <url>
    <loc>{site_url}/category.html</loc>
    <changefreq>daily</changefreq>
    <priority>0.8</priority>
  </url>""")

    # Individual article pages — high priority, they carry the SEO weight
    for a in data.get("articles", []):
        if a.get("status") != "published":
            continue
        aid = a["id"]
        pub_date = a.get("publishedAt", "")[:10]  # YYYY-MM-DD
        lastmod = f"<lastmod>{pub_date}</lastmod>" if pub_date else ""
        urls.append(f"""  <url>
    <loc>{site_url}/a/{aid}.html</loc>
    {lastmod}
    <changefreq>monthly</changefreq>
    <priority>0.9</priority>
  </url>""")

    sitemap = f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
{chr(10).join(urls)}
</urlset>"""

    sitemap_path = SCRIPT_DIR / "sitemap.xml"
    with open(sitemap_path, 'w') as f:
        f.write(sitemap)
    print(f"  ✓ Generated sitemap.xml with {len(urls)} URLs")


def generate_robots_txt():
    """Generate robots.txt allowing full indexing."""
    robots = """User-agent: *
Allow: /

Sitemap: https://themoldreport.org/sitemap.xml
"""
    robots_path = SCRIPT_DIR / "robots.txt"
    with open(robots_path, 'w') as f:
        f.write(robots)
    print("  ✓ Generated robots.txt")


def deploy_to_github():
    """Push updated files to GitHub Pages."""
    import subprocess
    repo_dir = Path(__file__).parent
    token = os.environ.get("GITHUB_TOKEN", "")
    repo_url = os.environ.get("GITHUB_REPO_URL", "")

    if not token or not repo_url:
        print("⚠ GITHUB_TOKEN or GITHUB_REPO_URL not set in .env — skipping deploy")
        return

    # Insert token into URL: https://x-access-token:TOKEN@github.com/user/repo.git
    if "github.com" in repo_url and "@" not in repo_url:
        auth_url = repo_url.replace("https://", f"https://x-access-token:{token}@")
    else:
        auth_url = repo_url

    try:
        # Check if git repo exists, if not init
        git_dir = repo_dir / ".git"
        if not git_dir.exists():
            subprocess.run(["git", "init"], cwd=repo_dir, check=True, capture_output=True)
            subprocess.run(["git", "checkout", "-b", "main"], cwd=repo_dir, check=True, capture_output=True)

        # Always set git identity (may be a fresh clone without global config)
        subprocess.run(["git", "config", "user.email", "bot@themoldreport.com"], cwd=repo_dir, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Mold Report Bot"], cwd=repo_dir, check=True, capture_output=True)

        # Stage only the files we want
        files_to_push = ["index.html", "articles.json", "editorial_pipeline.py", "scraper.py", "README.md", ".gitignore", "about.html", "generate_newsletter.py", "rewrite_headlines.py", "tips.json", "CNAME", "favicon.ico", "favicon-32x32.png", "apple-touch-icon.png", "og-image.png", "mold-101.html", "pipeline_config.json", "sync_transparency.py", "bootstrap.sh", "seed_backlog.py", "knowledge_corpus.json", "knowledge_compact.json", "sitemap.xml", "robots.txt", "category.html"]
        existing = [f for f in files_to_push if (repo_dir / f).exists()]
        subprocess.run(["git", "add"] + existing, cwd=repo_dir, check=True, capture_output=True)
        # Also add article share pages directory
        a_dir = repo_dir / "a"
        if a_dir.exists():
            subprocess.run(["git", "add", "a/"], cwd=repo_dir, check=True, capture_output=True)

        # Check if there are changes to commit
        result = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=repo_dir, capture_output=True)
        if result.returncode == 0:
            print("✓ No changes to deploy")
            return

        # Commit and push
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        subprocess.run(["git", "commit", "-m", f"Auto-publish: {now}"], cwd=repo_dir, check=True, capture_output=True)

        # Set remote
        subprocess.run(["git", "remote", "remove", "origin"], cwd=repo_dir, capture_output=True)
        subprocess.run(["git", "remote", "add", "origin", auth_url], cwd=repo_dir, check=True, capture_output=True)
        subprocess.run(["git", "push", "-u", "origin", "main", "--force"], cwd=repo_dir, check=True, capture_output=True)

        print("✓ Deployed to GitHub Pages")
    except subprocess.CalledProcessError as e:
        print(f"⚠ Deploy failed: {e}")
        if e.stderr:
            print(f"  stderr: {e.stderr.decode()[:200]}")


if __name__ == "__main__":
    main()
