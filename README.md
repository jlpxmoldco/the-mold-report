# The Mold Report

The first AI-curated mold news publication. Every article is sourced, rewritten, compliance-checked, and research-verified by AI editorial agents before it goes live.

## Quick Start

1. **Open `index.html`** in a browser to see the magazine
2. **Set up Google Alerts RSS** for the scraper (see below)
3. **Run the editorial pipeline** daily to pull fresh content

## File Structure

```
the-mold-report/
├── index.html              # The magazine (single file, host anywhere)
├── articles.json           # Article data (read by index.html)
├── editorial_pipeline.py   # AI editorial pipeline (Anthropic Claude API)
├── scraper.py              # Basic RSS scraper (no API key needed)
└── README.md               # You're here
```

## How It Works

Every article passes through a five-stage AI pipeline:

1. **Source** — Google Alerts RSS + reader-submitted tips feed real-time mold news
2. **Rewrite** — AI editorial agent rewrites raw headlines into clear, accessible journalism
3. **Compliance** — Every claim checked against medical compliance rules (no diagnosis language, approved statistics only, proper hedging)
4. **Research Verification** — Science and diagnostics articles verified for Shoemaker Protocol alignment
5. **Publish** — Only verified articles go live with full source attribution

## Setup

### 1. Google Alerts RSS Feed

1. Go to [Google Alerts](https://www.google.com/alerts)
2. Create alerts for: `mold illness`, `mold remediation`, `indoor mold`, `mold testing`
3. Set delivery to **RSS feed** (not email)
4. Copy the RSS feed URL

### 2. Install Dependencies

```bash
pip install anthropic feedparser requests beautifulsoup4
```

### 3. Set Your API Key

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
```

### 4. Run the Editorial Pipeline

```bash
# Full AI pipeline (requires Anthropic API key)
python editorial_pipeline.py --rss-url "https://www.google.com/alerts/feeds/YOUR_ID/YOUR_ALERT"

# Compliance-check existing articles
python editorial_pipeline.py --compliance-check

# Dry run (test without publishing)
python editorial_pipeline.py --dry-run

# Or use the basic scraper (no API key needed)
python scraper.py --rss-url "YOUR_RSS_URL" --auto-publish
```

### 5. Daily Schedule

**Mac/Linux (cron):**
```bash
# Run every day at 6am
0 6 * * * cd /path/to/the-mold-report && python editorial_pipeline.py --auto-publish
```

**Or use the Cowork scheduled task** (already configured, runs at 6am daily).

### 6. Hosting (GitHub Pages)

```bash
# Initialize repo
cd the-mold-report
git init
git add index.html articles.json editorial_pipeline.py scraper.py README.md
git commit -m "Initial commit: The Mold Report"

# Push to GitHub
gh repo create the-mold-report --public --source=. --push

# Enable GitHub Pages
# Go to Settings > Pages > Source: Deploy from branch > main > / (root) > Save
```

Other hosting options: Netlify (drag and drop), Vercel (`vercel deploy`), S3 + CloudFront.

## Features

| Feature | How It Works |
|---------|-------------|
| **AI Editorial Pipeline** | 5-stage: source, rewrite, compliance, research, publish |
| **Compliance Sub-Agent** | Checks every claim against MoldCo medical compliance rules |
| **Research Sub-Agent** | Verifies Shoemaker Protocol alignment for science articles |
| **RSS Scraping** | Pulls from Google Alerts RSS feed |
| **QC Layer** | Auto-checks title length, relevance, spam keywords |
| **Photo Layer** | Extracts og:image from source URLs, category stock fallbacks |
| **Category Filter** | Research, Regulation, News, Industry, Diagnostics |
| **News Submission** | Public form for reader tips |
| **Ad Banners** | MoldCo Starter Panel + Care banners built in |
| **SEO** | Schema.org NewsArticle, Open Graph, Twitter Cards, semantic HTML |
| **Admin View** | Add `?admin=true` to URL to see QC dashboard |
| **Newsletter Signup** | Email capture form (connect to your ESP) |
| **Mobile Responsive** | Full responsive design, mobile-first |

## Admin Mode

Add `?admin=true` to the URL to see the editorial dashboard showing published/review/draft counts.

## AI Transparency

The Mold Report is fully transparent about its AI-curated editorial process. The site includes:

- An **AI-Curated** badge in the edition bar (pulsing green dot)
- A **How We Work** section explaining the five-stage pipeline
- Full source attribution on every article
- "AI-curated" in all meta tags, Open Graph, and Schema.org markup

We believe AI journalism done right means radical transparency about the process.
