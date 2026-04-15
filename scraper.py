#!/usr/bin/env python3
"""
The Mold Report — Daily News Scraper
=====================================
Pulls articles from Google Alerts RSS, applies QC checks,
finds/assigns photos, and publishes to articles.json.

Usage:
  python scraper.py                          # Run full pipeline
  python scraper.py --rss-url "YOUR_URL"     # Custom RSS feed URL
  python scraper.py --add-manual             # Interactive: add a manual article
  python scraper.py --qc-review              # Review pending articles

Setup:
  1. Create a Google Alert for "mold" at https://www.google.com/alerts
  2. Set delivery to "RSS feed"
  3. Copy the RSS feed URL
  4. Set it as RSS_FEED_URL below or pass via --rss-url

Requirements:
  pip install feedparser requests beautifulsoup4
"""

import json
import os
import re
import hashlib
import argparse
from datetime import datetime, timezone
from pathlib import Path

try:
    import feedparser
except ImportError:
    feedparser = None
    print("⚠  feedparser not installed. Run: pip install feedparser")

try:
    import requests
except ImportError:
    requests = None
    print("⚠  requests not installed. Run: pip install requests")

try:
    from bs4 import BeautifulSoup
except ImportError:
    BeautifulSoup = None
    print("⚠  beautifulsoup4 not installed. Run: pip install beautifulsoup4")


# =========================================
# CONFIG
# =========================================
SCRIPT_DIR = Path(__file__).parent
ARTICLES_FILE = SCRIPT_DIR / "articles.json"
SUBMISSIONS_FILE = SCRIPT_DIR / "submissions.json"

# Replace with your Google Alerts RSS URL
RSS_FEED_URL = os.environ.get(
    "MOLD_REPORT_RSS",
    "https://www.google.com/alerts/feeds/YOUR_FEED_ID/YOUR_ALERT_ID"
)

# Unsplash fallback images by category
FALLBACK_IMAGES = {
    "research": "https://images.unsplash.com/photo-1559757175-5700dde675bc?w=800&q=80",
    "regulation": "https://images.unsplash.com/photo-1589829545856-d10d557cf95f?w=800&q=80",
    "news": "https://images.unsplash.com/photo-1504711434969-e33886168d6c?w=800&q=80",
    "industry": "https://images.unsplash.com/photo-1558618666-fcd25c85f82e?w=800&q=80",
    "diagnostics": "https://images.unsplash.com/photo-1579154204601-01588f351e67?w=800&q=80",
    "default": "https://images.unsplash.com/photo-1576091160550-2173dba999ef?w=800&q=80",
}

# QC: keyword blocklist (spam/irrelevant)
BLOCKLIST_KEYWORDS = [
    "crypto", "bitcoin", "nft", "casino", "viagra",
    "weight loss pill", "click here", "buy now",
]

# QC: required quality signals
MIN_TITLE_LENGTH = 20
MIN_SUMMARY_LENGTH = 50
MAX_TITLE_LENGTH = 200


# =========================================
# DATA LAYER
# =========================================
def load_articles():
    """Load existing articles from JSON file."""
    if ARTICLES_FILE.exists():
        with open(ARTICLES_FILE, "r") as f:
            return json.load(f)
    return {"lastUpdated": datetime.now(timezone.utc).isoformat(), "articles": []}


def save_articles(data):
    """Save articles to JSON file."""
    data["lastUpdated"] = datetime.now(timezone.utc).isoformat()
    with open(ARTICLES_FILE, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(f"✓ Saved {len(data['articles'])} articles to {ARTICLES_FILE.name}")


def generate_id(title):
    """Generate a deterministic ID from title to avoid duplicates."""
    return hashlib.md5(title.lower().strip().encode()).hexdigest()[:12]


# =========================================
# RSS FEED PARSER
# =========================================
def fetch_rss(url=None):
    """Fetch and parse Google Alerts RSS feed."""
    if feedparser is None:
        print("✗ feedparser required. Run: pip install feedparser")
        return []

    feed_url = url or RSS_FEED_URL
    if "YOUR_FEED_ID" in feed_url:
        print("⚠  No RSS feed URL configured.")
        print("   1. Go to https://www.google.com/alerts")
        print("   2. Create an alert for 'mold'")
        print("   3. Set delivery to 'RSS feed'")
        print("   4. Set MOLD_REPORT_RSS env var or pass --rss-url")
        return []

    print(f"→ Fetching RSS feed...")
    feed = feedparser.parse(feed_url)

    articles = []
    for entry in feed.entries:
        title = clean_html(entry.get("title", ""))
        summary = clean_html(entry.get("summary", entry.get("description", "")))
        link = entry.get("link", "")
        published = entry.get("published", "")

        if not title:
            continue

        # Parse published date
        pub_date = datetime.now(timezone.utc).isoformat()
        if published:
            try:
                from email.utils import parsedate_to_datetime
                pub_date = parsedate_to_datetime(published).isoformat()
            except Exception:
                pass

        # Extract source from URL
        source = extract_source(link)

        article = {
            "id": generate_id(title),
            "title": title,
            "summary": summary,
            "source": source,
            "sourceUrl": link,
            "author": source,
            "publishedAt": pub_date,
            "category": classify_category(title + " " + summary),
            "imageUrl": "",
            "imageAlt": "",
            "status": "review",  # All RSS articles start in review
            "qcReviewer": "",
            "qcTimestamp": "",
            "tags": extract_tags(title + " " + summary),
            "featured": False,
            "readTime": estimate_read_time(summary),
        }
        articles.append(article)

    print(f"→ Found {len(articles)} articles from RSS")
    return articles


def clean_html(text):
    """Strip HTML tags from text."""
    if BeautifulSoup:
        return BeautifulSoup(text, "html.parser").get_text(strip=True)
    return re.sub(r"<[^>]+>", "", text).strip()


def extract_source(url):
    """Extract publication name from URL."""
    if not url:
        return "Unknown"
    try:
        from urllib.parse import urlparse
        domain = urlparse(url).netloc.replace("www.", "")
        # Map common domains to nice names
        domain_map = {
            "reuters.com": "Reuters",
            "nytimes.com": "New York Times",
            "washingtonpost.com": "Washington Post",
            "cnn.com": "CNN",
            "bbc.com": "BBC",
            "nature.com": "Nature",
            "sciencedirect.com": "ScienceDirect",
            "pubmed.ncbi.nlm.nih.gov": "PubMed",
            "techcrunch.com": "TechCrunch",
        }
        return domain_map.get(domain, domain.split(".")[0].title())
    except Exception:
        return "Unknown"


# =========================================
# CLASSIFICATION
# =========================================
def classify_category(text):
    """Auto-classify article into a category based on keywords."""
    text_lower = text.lower()

    categories = {
        "research": ["study", "research", "findings", "published", "journal",
                      "scientists", "data", "clinical", "trial", "peer-reviewed",
                      "university", "lab", "experiment"],
        "regulation": ["law", "regulation", "epa", "legislation", "bill",
                        "compliance", "policy", "government", "federal", "state law",
                        "mandate", "guideline", "standard"],
        "diagnostics": ["test", "biomarker", "blood test", "diagnostic",
                         "screening", "panel", "lab", "mycotoxin", "assay"],
        "industry": ["company", "startup", "funding", "market", "business",
                      "product", "launch", "investment", "revenue", "ipo",
                      "acquisition", "technology"],
    }

    scores = {}
    for cat, keywords in categories.items():
        scores[cat] = sum(1 for kw in keywords if kw in text_lower)

    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else "news"


def extract_tags(text):
    """Extract relevant tags from article text."""
    tag_keywords = [
        "EPA", "WHO", "CDC", "mold", "mycotoxin", "remediation",
        "air quality", "biomarker", "Stachybotrys", "Aspergillus",
        "CIRS", "brain fog", "inflammation", "housing", "school",
        "insurance", "lawsuit", "testing", "treatment", "health",
        "climate", "flooding", "hurricane", "water damage",
    ]
    text_lower = text.lower()
    return [tag for tag in tag_keywords if tag.lower() in text_lower][:6]


def estimate_read_time(text):
    """Estimate read time in minutes (200 words/min average)."""
    words = len(text.split())
    return max(2, round(words / 200) + 2)  # +2 for original article


# =========================================
# PHOTO LAYER
# =========================================
def assign_photos(articles):
    """Assign images to articles that don't have one."""
    for article in articles:
        if not article.get("imageUrl"):
            # Try to extract image from source page
            img = try_extract_og_image(article.get("sourceUrl", ""))
            if img:
                article["imageUrl"] = img
                article["imageAlt"] = article["title"][:80]
            else:
                # Fallback to category-based stock photo
                cat = article.get("category", "default")
                article["imageUrl"] = FALLBACK_IMAGES.get(cat, FALLBACK_IMAGES["default"])
                article["imageAlt"] = f"{cat.title()} related image"

    return articles


def try_extract_og_image(url):
    """Try to extract Open Graph image from article URL."""
    if not url or not requests or not BeautifulSoup:
        return None

    try:
        headers = {"User-Agent": "TheMoldReport/1.0 (news aggregator)"}
        resp = requests.get(url, headers=headers, timeout=5)
        soup = BeautifulSoup(resp.text, "html.parser")

        # Try og:image first
        og = soup.find("meta", property="og:image")
        if og and og.get("content"):
            return og["content"]

        # Try twitter:image
        tw = soup.find("meta", attrs={"name": "twitter:image"})
        if tw and tw.get("content"):
            return tw["content"]

    except Exception:
        pass

    return None


# =========================================
# QC LAYER
# =========================================
def run_qc(articles):
    """
    Quality control checks on articles.
    Returns (passed, failed) tuple of article lists.
    """
    passed = []
    failed = []

    for article in articles:
        issues = []

        # Check title length
        if len(article["title"]) < MIN_TITLE_LENGTH:
            issues.append(f"Title too short ({len(article['title'])} chars)")
        if len(article["title"]) > MAX_TITLE_LENGTH:
            issues.append(f"Title too long ({len(article['title'])} chars)")

        # Check summary length
        if len(article["summary"]) < MIN_SUMMARY_LENGTH:
            issues.append(f"Summary too short ({len(article['summary'])} chars)")

        # Check for blocklisted content
        combined = (article["title"] + " " + article["summary"]).lower()
        for keyword in BLOCKLIST_KEYWORDS:
            if keyword in combined:
                issues.append(f"Blocklisted keyword: '{keyword}'")

        # Check for duplicate-ish titles (all caps, excessive punctuation)
        if article["title"].isupper():
            issues.append("Title is all caps")
        if article["title"].count("!") > 1:
            issues.append("Excessive exclamation marks")

        # Check relevance (must mention mold-related terms)
        mold_terms = ["mold", "mould", "mycotoxin", "fungal", "spore",
                       "indoor air", "remediation", "water damage", "damp"]
        if not any(term in combined for term in mold_terms):
            issues.append("May not be mold-related")

        if issues:
            article["_qc_issues"] = issues
            article["status"] = "review"
            failed.append(article)
            print(f"  ✗ QC FAIL: {article['title'][:60]}...")
            for issue in issues:
                print(f"    - {issue}")
        else:
            article["status"] = "review"  # Still needs human review
            passed.append(article)
            print(f"  ✓ QC PASS: {article['title'][:60]}...")

    return passed, failed


# =========================================
# MANUAL ARTICLE ENTRY
# =========================================
def add_manual_article():
    """Interactive prompt to add a manual article."""
    print("\n=== Add Manual Article ===\n")

    title = input("Title: ").strip()
    if not title:
        print("Cancelled.")
        return None

    summary = input("Summary: ").strip()
    source = input("Source (e.g., 'Reuters'): ").strip() or "Staff"
    source_url = input("Source URL: ").strip()
    author = input("Author: ").strip() or source

    print("\nCategories: research, regulation, news, industry, diagnostics")
    category = input("Category: ").strip() or "news"

    image_url = input("Image URL (leave blank for auto): ").strip()
    featured = input("Featured? (y/n): ").strip().lower() == "y"

    article = {
        "id": generate_id(title),
        "title": title,
        "summary": summary,
        "source": source,
        "sourceUrl": source_url,
        "author": author,
        "publishedAt": datetime.now(timezone.utc).isoformat(),
        "category": category,
        "imageUrl": image_url,
        "imageAlt": title[:80] if image_url else "",
        "status": "published",  # Manual articles bypass QC
        "qcReviewer": "Manual Entry",
        "qcTimestamp": datetime.now(timezone.utc).isoformat(),
        "tags": extract_tags(title + " " + summary),
        "featured": featured,
        "readTime": estimate_read_time(summary),
    }

    print(f"\n✓ Article created: {title[:60]}...")
    return article


# =========================================
# QC REVIEW MODE
# =========================================
def qc_review_mode(data):
    """Interactive review of pending articles."""
    pending = [a for a in data["articles"] if a.get("status") == "review"]

    if not pending:
        print("✓ No articles pending review.")
        return data

    print(f"\n=== QC Review: {len(pending)} articles pending ===\n")

    for i, article in enumerate(pending):
        print(f"\n--- Article {i+1}/{len(pending)} ---")
        print(f"Title:    {article['title']}")
        print(f"Source:   {article['source']}")
        print(f"Category: {article['category']}")
        print(f"Summary:  {article['summary'][:200]}...")

        if article.get("_qc_issues"):
            print(f"QC Issues: {', '.join(article['_qc_issues'])}")

        action = input("\n[p]ublish / [s]kip / [d]elete / [e]dit title / [q]uit: ").strip().lower()

        if action == "p":
            article["status"] = "published"
            article["qcReviewer"] = "Editor"
            article["qcTimestamp"] = datetime.now(timezone.utc).isoformat()
            if "_qc_issues" in article:
                del article["_qc_issues"]
            print("  → Published")
        elif action == "d":
            article["status"] = "deleted"
            print("  → Deleted")
        elif action == "e":
            new_title = input("  New title: ").strip()
            if new_title:
                article["title"] = new_title
            article["status"] = "published"
            article["qcReviewer"] = "Editor"
            article["qcTimestamp"] = datetime.now(timezone.utc).isoformat()
            print("  → Edited and published")
        elif action == "q":
            break
        else:
            print("  → Skipped (stays in review)")

    # Remove deleted articles
    data["articles"] = [a for a in data["articles"] if a.get("status") != "deleted"]
    return data


# =========================================
# MAIN PIPELINE
# =========================================
def run_pipeline(rss_url=None):
    """Full scrape → QC → photo → publish pipeline."""
    print("=" * 50)
    print("  The Mold Report — Daily Scraper")
    print("=" * 50)

    # 1. Load existing articles
    data = load_articles()
    existing_ids = {a["id"] for a in data["articles"]}
    print(f"\n→ {len(existing_ids)} existing articles")

    # 2. Fetch RSS
    new_articles = fetch_rss(rss_url)

    # 3. Deduplicate
    fresh = [a for a in new_articles if a["id"] not in existing_ids]
    print(f"→ {len(fresh)} new articles after dedup")

    if not fresh:
        print("→ No new articles to process.")
        return

    # 4. QC
    print("\n--- QC Check ---")
    passed, failed = run_qc(fresh)
    print(f"\n→ {len(passed)} passed QC, {len(failed)} flagged for review")

    # 5. Photo layer
    print("\n--- Photo Layer ---")
    all_new = passed + failed
    all_new = assign_photos(all_new)
    print(f"→ Photos assigned to {len(all_new)} articles")

    # 6. Merge and save
    data["articles"] = all_new + data["articles"]

    # Sort by date (newest first)
    data["articles"].sort(key=lambda a: a.get("publishedAt", ""), reverse=True)

    # 7. Set featured (top 2 published articles)
    for a in data["articles"]:
        a["featured"] = False
    published = [a for a in data["articles"] if a["status"] == "published"]
    for a in published[:2]:
        a["featured"] = True

    save_articles(data)

    # Summary
    total_published = len([a for a in data["articles"] if a["status"] == "published"])
    total_review = len([a for a in data["articles"] if a["status"] == "review"])
    print(f"\n{'=' * 50}")
    print(f"  Done. {total_published} published, {total_review} in review.")
    print(f"{'=' * 50}")


# =========================================
# CLI
# =========================================
def main():
    parser = argparse.ArgumentParser(description="The Mold Report — Daily Scraper")
    parser.add_argument("--rss-url", help="Google Alerts RSS feed URL")
    parser.add_argument("--add-manual", action="store_true", help="Add a manual article")
    parser.add_argument("--qc-review", action="store_true", help="Review pending articles")
    parser.add_argument("--auto-publish", action="store_true",
                        help="Auto-publish articles that pass QC (for scheduled runs)")
    args = parser.parse_args()

    if args.add_manual:
        data = load_articles()
        article = add_manual_article()
        if article:
            article = assign_photos([article])[0]
            data["articles"].insert(0, article)
            save_articles(data)
    elif args.qc_review:
        data = load_articles()
        data = qc_review_mode(data)
        save_articles(data)
    else:
        run_pipeline(args.rss_url)

        # Auto-publish mode: approve all QC-passed articles
        if args.auto_publish:
            data = load_articles()
            count = 0
            for a in data["articles"]:
                if a.get("status") == "review" and not a.get("_qc_issues"):
                    a["status"] = "published"
                    a["qcReviewer"] = "Auto-QC"
                    a["qcTimestamp"] = datetime.now(timezone.utc).isoformat()
                    count += 1
            if count:
                print(f"→ Auto-published {count} articles that passed QC")
                save_articles(data)


if __name__ == "__main__":
    main()
