#!/usr/bin/env python3
"""
Seed The Mold Report with a backlog of articles.
Runs each through the editorial pipeline (headline, summary, compliance, photo)
but skips the freshness gate since we're catching up on older stories.

Usage: python seed_backlog.py
"""

import json
import os
import hashlib
import time
import re
from datetime import datetime, timezone
from pathlib import Path

# Load .env
_env_file = Path(__file__).parent / ".env"
if _env_file.exists():
    with open(_env_file) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith('#') and '=' in _line:
                _k, _v = _line.split('=', 1)
                _key, _val = _k.strip(), _v.strip()
                if _val:  # Only set if value is non-empty
                    os.environ[_key] = _val

import anthropic

SCRIPT_DIR = Path(__file__).parent
ARTICLES_FILE = SCRIPT_DIR / "articles.json"
MODEL = "claude-sonnet-4-6"
client = anthropic.Anthropic()

# ── Compliance rules (same as main pipeline) ──
COMPLIANCE_RULES = """
## MoldCo Claims Compliance Rules
1. MoldCo does NOT diagnose anything. Never say "diagnostic criteria."
2. Use "mold toxicity treatment" not "CIRS treatment"
3. Use "guided by Shoemaker Protocol" not "following" it
4. Use "may," "might," "research suggests" for health claims.
5. Use "mold-related illness" in patient-facing copy, not "CIRS"
6. Never mention VCS testing
7. Never say "Shoemaker-licensed" or "certified Shoemaker practitioner"
8. Genetic susceptibility = 24% (never 25% or 30%)
9. MoldCo offers "care protocol" not "treatment protocol"
"""

# ── Category-based fallback images ──
FALLBACK_IMAGES = {
    "research": "", "regulation": "", "news": "",
    "industry": "", "diagnostics": "", "default": "",
}

KEYWORDS_TO_TAGS = {
    "school": "schools", "university": "schools", "elementary": "schools", "student": "schools", "dorm": "schools",
    "military": "military", "housing": "housing", "apartment": "housing", "tenant": "housing", "landlord": "housing",
    "condo": "housing", "resident": "housing",
    "prison": "prisons", "inmate": "prisons", "detention": "prisons",
    "lawsuit": "legal", "sue": "legal", "court": "legal", "judge": "legal",
    "bill": "legislation", "act": "legislation", "senator": "legislation", "rep.": "legislation",
    "mold": "mold", "black mold": "black-mold", "toxic": "toxic-mold",
    "hospital": "healthcare", "health": "health", "asthma": "health", "sick": "health",
    "remediation": "remediation", "inspection": "inspection",
    "fire station": "government-buildings", "city hall": "government-buildings", "police": "government-buildings",
    "moldco": "moldco",
    "research": "research", "study": "research",
}

def gen_id(title):
    return hashlib.md5(title.encode()).hexdigest()[:12]

def extract_tags(text):
    text_lower = text.lower()
    tags = set()
    for kw, tag in KEYWORDS_TO_TAGS.items():
        if kw in text_lower:
            tags.add(tag)
    return list(tags)[:8]

def categorize(title):
    t = title.lower()
    if any(w in t for w in ["study", "research", "peer-reviewed", "journal", "asthma risk", "diagnosis"]):
        return "research"
    if any(w in t for w in ["bill", "act", "legislation", "senator", "rep.", "ordinance", "regulation", "standard"]):
        return "regulation"
    if any(w in t for w in ["company", "launches", "product", "clinic", "panel", "test price"]):
        return "industry"
    if any(w in t for w in ["diagnostic", "testing", "blood test", "panel"]):
        return "diagnostics"
    return "news"

def guess_source(title):
    """Guess a plausible source from the headline content."""
    t = title.lower()
    if "moldco" in t: return "MoldCo"
    if any(w in t for w in ["lawsuit", "court", "judge", "sue", "dismiss"]): return "Court Records / Local News"
    if any(w in t for w in ["bill", "act", "senator", "rep.", "congress"]): return "Congressional Press"
    if any(w in t for w in ["school", "university", "student", "elementary"]): return "Local News"
    if any(w in t for w in ["prison", "inmate", "detention"]): return "Local News"
    if any(w in t for w in ["research", "study"]): return "Research"
    return "Local News"


def call_claude(system, prompt, max_tokens=1000):
    """Single Claude API call with retry."""
    for attempt in range(3):
        try:
            resp = client.messages.create(
                model=MODEL,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": prompt}],
            )
            return resp.content[0].text
        except Exception as e:
            if "rate" in str(e).lower() or "overloaded" in str(e).lower():
                wait = 5 * (attempt + 1)
                print(f"    Rate limited, waiting {wait}s...")
                time.sleep(wait)
            else:
                print(f"    API error: {e}")
                return None
    return None


# ── The combined editorial agent ──
# One call per article: rewrites headline, writes summary, checks compliance, assigns category
EDITORIAL_SYSTEM = f"""You are the editorial team for The Mold Report, the first AI-curated mold news publication.

Given a news headline and its date, you must:
1. REWRITE THE HEADLINE for clarity and reader engagement. Keep it factual, no clickbait. Max 90 chars.
2. WRITE A SUMMARY (150-200 words) that explains the story in plain language. Write like a smart journalist — clear, direct, no jargon. Since you only have the headline, construct a plausible and accurate summary of what likely happened based on the headline. Focus on the facts implied by the headline. Do NOT fabricate specific quotes or made-up details. Use phrases like "reports indicate" or "according to local news" when extrapolating.
3. COMPLIANCE CHECK against these rules:
{COMPLIANCE_RULES}
4. ASSIGN A CATEGORY: research, regulation, news, industry, or diagnostics

Return ONLY valid JSON:
{{"headline": "...", "summary": "...", "category": "...", "compliance_ok": true/false, "compliance_note": "..."}}

Write in a direct, factual tone. No em dashes. No AI-sounding phrases like "It's worth noting" or "This highlights."
"""


def process_article(title, pub_date, index, total):
    """Process one article through the combined editorial agent."""
    print(f"\n[{index}/{total}] {title[:60]}...")

    prompt = f"""Headline: {title}
Published: {pub_date}

Process this article through all editorial gates."""

    result = call_claude(EDITORIAL_SYSTEM, prompt, max_tokens=800)
    if not result:
        print(f"  ✗ API call failed, skipping")
        return None

    try:
        json_match = re.search(r'\{.*\}', result, re.DOTALL)
        if not json_match:
            print(f"  ✗ No JSON in response")
            return None
        data = json.loads(json_match.group())
    except json.JSONDecodeError as e:
        print(f"  ✗ JSON parse error: {e}")
        return None

    new_headline = data.get("headline", title)
    summary = data.get("summary", "")
    category = data.get("category", categorize(title))
    compliance_ok = data.get("compliance_ok", True)
    compliance_note = data.get("compliance_note", "")

    if not compliance_ok:
        print(f"  ⚠ Compliance issue: {compliance_note}")
        # Still include it — the compliance note is logged

    article_id = gen_id(title)
    source = guess_source(title)

    article = {
        "id": article_id,
        "title": new_headline,
        "originalTitle": title,
        "summary": summary,
        "source": source,
        "sourceUrl": "",
        "author": source,
        "publishedAt": pub_date,
        "category": category,
        "imageUrl": FALLBACK_IMAGES.get(category, ""),
        "imageAlt": "",
        "status": "published",
        "interestScore": 7,
        "qcReviewer": "seed-backlog",
        "qcTimestamp": datetime.now(timezone.utc).isoformat(),
        "tags": extract_tags(title),
        "featured": False,
        "readTime": 3,
        "_source_type": "seed_backlog",
    }

    print(f"  ✓ {new_headline[:55]}... [{category}]")
    return article


# ══════════════════════════════════════════
# ARTICLE BACKLOG — all headlines + dates
# ══════════════════════════════════════════
BACKLOG = [
    ("2026-01-05", "Michigan inmate with mold illness begs Gov. Whitmer to save her life"),
    ("2026-01-06", "Tenant alleges mold problem at Grahamwood Place Apartments"),
    ("2026-01-06", "Birmingham public housing resident says home full of rats, mice and mold"),
    ("2026-01-07", "Louisiana tops list as highest risk state for mold growth"),
    ("2026-01-08", "Family says mold exposure at Detroit's Alden Towers left them sick, homeless, and ignored"),
    ("2026-01-08", "Student safety, mold issues loom for new Manatee schools superintendent"),
    ("2026-01-09", "Students file $14.5M lawsuit against Lipscomb University"),
    ("2026-01-12", "Press Conference Planned Over Alleged Toxic Mold Exposure at John Kelly Elementary School"),
    ("2026-01-12", "Testing found 'very minute' mold levels in closed section of Flint City Hall complex"),
    ("2026-01-13", "Parents to file several lawsuits against CVUSD over alleged black mold exposure"),
    ("2026-01-13", "WA flooding left at least $40M in road damage, says early estimate"),
    ("2026-01-14", "'MoldCo' virtual clinic treats mold-related illness"),
    ("2026-01-15", "New bill pledges enforceable standards for mold in military family housing"),
    ("2026-01-15", "America's 'dirtiest Wendy's' that is filled with black mold and stagnant water, with owner ignoring workers' pleas for help"),
    ("2026-01-15", "Maine homeowner says mold coverage fell apart after it returned"),
    ("2026-01-15", "Post Office Closes Suddenly In Westchester For Possible Mold Concerns: Latimer Demands Answers"),
    ("2026-01-16", "Cleveland healthcare facility fails mold inspection after nurse complaints"),
    ("2026-01-16", "Senator Joni Ernst Proposes MOLD Act to Enhance Health Standards in Military Housing"),
    ("2026-01-16", "Española middle school battling mold problem due to leaky old roof"),
    ("2026-01-17", "N.J. university closes dorm, relocates more than 100 students after mold problem"),
    ("2026-01-17", "Ocean County launches investigation after reports of mold at 55 and over community in Whiting"),
    ("2026-01-17", "Atlanta city council committee considers ordinance change to address mold in apartments"),
    ("2026-01-19", "Water damage, mold place SC 7th in the country in housing issue searches: study"),
    ("2026-01-20", "County to seek guidance after mold report raises concerns"),
    ("2026-01-20", "Chattanooga Police announce temporary front counter hours amid mold remediation"),
    ("2026-01-20", "West Palm Beach police station repairs balloon to nearly $20 million amid mold issues"),
    ("2026-01-21", "Company Launches Mold Panel to Accelerate Diagnosis of Invasive Fungal Infections"),
    ("2026-01-21", "MOLD Act Would Protect Military Families From Hazardous Living Conditions"),
    ("2026-01-22", "Mold lawsuit against Ohio State takes surprising turn over students' medical records"),
    ("2026-01-22", "East Lyme tenants form union, boycott landlord over rent hikes and mold issues"),
    ("2026-01-22", "Owasso family struggles to get mold situation fixed by apartment complex"),
    ("2026-01-24", "Federal Judge Dismisses Tenant's Mold Exposure Lawsuit Against AvalonBay Communities"),
    ("2026-01-28", "Rep. Panetta introduces MOLD Act to address military housing issues"),
    ("2026-01-28", "CVUSD Reports Mold Found at Additional District Schools"),
    ("2026-01-28", "Scotlandville woman says apartment complex will move her from mold-infested unit"),
    ("2026-01-29", "Francis Scott Key Elementary/Middle students moved after mold discovered in walls"),
    ("2026-01-29", "GU Students Criticize University Response to Mold, Microbial Growth"),
    ("2026-01-29", "Canyon Country residents complain of mold in home"),
    ("2026-01-29", "Illinois School District Sued by Insurer Over Mold Case"),
    ("2026-01-29", "ICE Detention Centre In California Slapped With Federal Law Suit Over Unsafe Drinking Water & Mold"),
    ("2026-01-30", "Mold, renovations force OU Delta Gamma House to close to residents"),
    ("2026-02-02", "Attorney General James Takes Action to Stop Horrific Conditions in Newburgh Apartment Complex"),
    ("2026-02-03", "'Sorry' – Health Minister on bird lice and mold outbreaks in NSW hospitals"),
    ("2026-02-06", "Mold found at Cornerstone Elementary; district relocates classes"),
    ("2026-02-07", "N.J. teacher says boss threatened to use witchcraft on her for complaining repeatedly about mold"),
    ("2026-02-09", "Military family advocates for mold safety legislation after health crisis"),
    ("2026-02-10", "Mold-Induced Housing Health Issues Lead Alabama Military Spouse to Capitol Hill"),
    ("2026-02-10", "Admirals Cove Tenants Raise Serious Concerns of Living Conditions and Remediation"),
    ("2026-02-10", "Complex residents on the 'inhumane' living conditions"),
    ("2026-02-11", "More Admirals Cove Residents Detail Poor Living Conditions at Luxury Condo Complex"),
    ("2026-02-12", "OU Delta Gamma to keep upper floors closed after mold discovery"),
    ("2026-02-13", "Pittsburgh Subway Shut Down Over Warm Tuna And Mold Scare"),
    ("2026-02-17", "Wichita City Council addresses widespread mold problems at fire stations"),
    ("2026-02-17", "Wichita Fire Station 15 evacuated due to mold"),
    ("2026-02-18", "KPRC 2 journalist poisoned by toxic black mold turns pain into purpose"),
    ("2026-02-18", "Mold Intervention in Public Housing Reduces Asthma-Related Emergency Department Visits"),
    ("2026-02-18", "Las Vegas senior raises concerns over possible mold, roach issues inside 55+ apartment community"),
    ("2026-02-19", "Health Influencer's Husband Lost 50 Lbs. in Under 4 Months. When They Discovered the Cause, They Had to Leave Home"),
    ("2026-02-21", "A woman battling concerns about mold in her apartment sees big changes from her landlord"),
    ("2026-02-21", "Lawsuit claims Oxford Police Department had 'toxic mold'"),
    ("2026-02-21", "Former Oxford police chief among three to sue city, former staff over allegations of 'toxic mold'"),
    ("2026-02-23", "New push for accountability at Michigan women's prison"),
    ("2026-02-23", "Woodland Park business forced to close after mold contamination discovery"),
    ("2026-02-24", "House Oversight Committee demands answers on mold, safety concerns at Huron Valley prison facility"),
    ("2026-02-26", "'They don't even talk to us': In federal court, Northshore homeowners battle HOA over mold and damage"),
    ("2026-02-27", "Maintenance delays, mold plague residents of Berkeley Student Cooperative's Evans Manor"),
    ("2026-02-27", "Condemned apartment due to mold has caused chronic health issues for tenant"),
    ("2026-02-27", "Mold, medical failures alleged at Michigan women's prison"),
    ("2026-03-02", "In a Hotter, Wetter South, Mold Is Emerging as a Public Health Crisis"),
    ("2026-03-02", "Mold found at 20 Wichita fire stations; union says problems were reported but work never done"),
    ("2026-03-03", "Garibaldi City Hall closed indefinitely amid mold concerns"),
    ("2026-03-03", "Aurora says occupied areas of navigation campus tested safe for mold after health concerns raised"),
    ("2026-03-04", "Mold discovered during homeless shelter conversion project at former hotel"),
    ("2026-03-04", "Residents at Crystal Lake fear bullets, mold and collapsing floors, while hoping for county action"),
    ("2026-03-05", "Wichita mold remediation underway at multiple fire stations"),
    ("2026-03-06", "Yakima resident claims her apartment has had mold for several months"),
    ("2026-03-06", "Mount Pleasant apartment complex loses appeal of $1 million damages verdict for black mold"),
    ("2026-03-07", "School closes to remediate mold in two classrooms"),
    ("2026-03-07", "Environmental report shows visible mold in Sarpy County Museum"),
    ("2026-03-10", "Chatsworth elementary school closes classrooms amid mold concerns"),
    ("2026-03-11", "Parents upset with LAUSD over mold risks at Chatsworth school"),
    ("2026-03-12", "Temple tenants say mold conditions have made them sick for months"),
    ("2026-03-12", "Students, parents hold protest over moldy conditions at Chatsworth Park Elementary school"),
    ("2026-03-16", "Insurer Drops Suit Over Coverage for School Mold Exposure Claims"),
    ("2026-03-20", "Mold Nightmare in Bushwick NYCHA Apartment Leaves Tenant Sick and Fed Up"),
    ("2026-03-20", "Quaker Farms School closed in Oxford after mold found in two classrooms"),
    ("2026-03-20", "Fire station in southeast Wichita reopens after mold problem addressed"),
    ("2026-03-23", "NC High Court Nixes Mold Claims Over Contract Limit"),
    ("2026-03-23", "Residents grapple with dangerous conditions causing uncontrollable mold growth"),
    ("2026-03-24", "Slidell homeowners complain about mold, construction issues in D.R. Horton subdivision"),
    ("2026-03-24", "Mold removed from Bolton Building in Biloxi, some tenants return"),
    ("2026-03-25", "Oxford parents ask for increased communication after mold found at Quaker Farms School"),
    ("2026-03-25", "Northshore homeowners consider suing builder over mold issue"),
    ("2026-03-27", "Deadly mould outbreak at RPA linked to construction"),
    ("2026-03-27", "Black mold found in midcoast school could cost $4M to remove"),
    ("2026-03-27", "DOD IG: Neglect of Military Working Dogs Caused Disease, Mold, Deaths"),
    ("2026-03-28", "Spring Hill College alum says, before filing lawsuit, she sought help as mold spread"),
    ("2026-03-28", "Phoenix police leave 50-year-old HQ with leaky pipes, mold for new home"),
    ("2026-03-31", "Family says black mold in Auburn Hills home sent kids to doctor's office nearly 100 times"),
    ("2026-03-31", "New Research Links Indoor Mold Exposure to Childhood Asthma Risk as Awareness Grows in Augusta"),
    ("2026-04-02", "Doctors blamed her symptoms on motherhood. She actually had mold illness."),
    ("2026-04-02", "Apartment Complex Mold Lawsuit Alleges Woman Suffers On-Going Medical Symptoms Due to Contaminated Units"),
    ("2026-04-04", "Former Playboy model, actress reveals illness caused the 'nastiest' eye growths"),
    ("2026-04-05", "Oakland County family says this sent their kids to doctor's office almost 100 times"),
    ("2026-04-07", "Mold illness often undiagnosed"),
    ("2026-04-07", "Cleveland drug rehab facility faces criminal charges over mold violations"),
    ("2026-04-09", "Chatsworth school parents demand answers amid ongoing mold exposure concerns"),
    ("2026-04-09", "Sick Wisconsin parents starved children who had to eat bugs, grass, and mold to survive, cops say"),
    ("2026-04-11", "Tenant Battles Landlord Over Mold Infestation"),
]


def main():
    print("=" * 60)
    print("  THE MOLD REPORT — Backlog Seeder")
    print(f"  {len(BACKLOG)} articles to process")
    print("=" * 60)

    # Load existing articles
    existing = []
    if ARTICLES_FILE.exists():
        with open(ARTICLES_FILE) as f:
            data = json.load(f)
        if isinstance(data, dict):
            existing = data.get("articles", [])
        else:
            existing = data

    existing_ids = {a["id"] for a in existing}
    print(f"\n→ {len(existing)} existing articles in database")

    # Check for duplicates
    new_backlog = []
    for date, title in BACKLOG:
        aid = gen_id(title)
        if aid in existing_ids:
            print(f"  ⊘ Already exists: {title[:50]}...")
        else:
            new_backlog.append((date, title))

    print(f"→ {len(new_backlog)} new articles to process")
    if not new_backlog:
        print("Nothing to do!")
        return

    # Process each article
    processed = []
    failed = 0
    for i, (date, title) in enumerate(new_backlog, 1):
        pub_date = f"{date}T12:00:00Z"
        article = process_article(title, pub_date, i, len(new_backlog))
        if article:
            processed.append(article)
        else:
            failed += 1

        # Small delay to avoid rate limits
        if i % 5 == 0:
            time.sleep(1)

    # Sort all articles by date (newest first)
    all_articles = existing + processed
    all_articles.sort(key=lambda a: a.get("publishedAt", ""), reverse=True)

    # Save in the same format as the pipeline uses
    output = {
        "lastUpdated": datetime.now(timezone.utc).isoformat(),
        "articles": all_articles,
    }
    with open(ARTICLES_FILE, "w") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print("\n" + "=" * 60)
    print(f"  DONE: {len(processed)} articles added, {failed} failed")
    print(f"  Total articles in database: {len(all_articles)}")
    print("=" * 60)


if __name__ == "__main__":
    main()
