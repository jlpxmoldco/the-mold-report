#!/usr/bin/env python3
"""One-shot: run the headline agent on all existing articles."""

import json, os, re, time
from pathlib import Path

# Load .env
_env_file = Path(__file__).parent / ".env"
if _env_file.exists():
    with open(_env_file) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                k, v = line.split('=', 1)
                os.environ.setdefault(k.strip(), v.strip())

import anthropic

MODEL = "claude-sonnet-4-6"
ARTICLES_FILE = Path(__file__).parent / "articles.json"

def call_claude(system_prompt, user_prompt, max_tokens=200):
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    client = anthropic.Anthropic(api_key=api_key)
    for attempt in range(3):
        try:
            message = client.messages.create(
                model=MODEL, max_tokens=max_tokens,
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
            print(f"  ⚠ Error: {e}")
            return None

HEADLINE_SYSTEM = """You are the headline editor for The Mold Report, the first AI-curated mold news publication.

Your job is to rewrite article titles to make people WANT to read. You're writing for a smart audience that cares about mold exposure, health, and science — but they still need a reason to click.

RULES FOR GREAT HEADLINES:
1. Lead with the most interesting finding or implication, not the method
2. Use plain language. Never use academic jargon in the headline
3. Be specific: include numbers, names, institutions when they add punch
4. Keep it under 90 characters (strict limit)
5. Never use clickbait ("You won't believe..."), all-caps, or exclamation marks
6. Never misrepresent the content — accuracy is sacred
7. Use active voice. "Study Finds X" not "X Was Found By Study"
8. For research papers: translate the finding into what it MEANS for real people
9. Drop unnecessary words (a, the, that) where it reads naturally

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


def rewrite_headline(article):
    title = article['title']
    prompt = f"""Rewrite this headline for The Mold Report:

Original title: {title}
Category: {article['category']}
Summary excerpt: {article['summary'][:300]}
Source: {article['source']}"""

    result = call_claude(HEADLINE_SYSTEM, prompt)
    if result:
        try:
            json_match = re.search(r'\{.*\}', result, re.DOTALL)
            if json_match:
                review = json.loads(json_match.group())
                if review.get('changed', False) and review.get('rewritten'):
                    new_title = review['rewritten'].strip()
                    if len(new_title) <= 100 and len(new_title) > 10:
                        return new_title, review.get('reasoning', '')
                    else:
                        return None, f"Rewrite too {'long' if len(new_title)>100 else 'short'}: {new_title}"
                else:
                    return None, review.get('reasoning', 'Already good')
        except (json.JSONDecodeError, AttributeError) as e:
            return None, f"Parse error: {e}"
    return None, "No response"


if __name__ == "__main__":
    with open(ARTICLES_FILE) as f:
        data = json.load(f)

    changes = []
    for i, art in enumerate(data['articles']):
        print(f"\n[{i+1}/{len(data['articles'])}] {art['title'][:70]}...")
        new_title, reason = rewrite_headline(art)
        if new_title:
            print(f"  → NEW: {new_title}")
            print(f"    Why: {reason}")
            art['_original_title'] = art['title']
            art['title'] = new_title
            changes.append((art['id'], art['_original_title'], new_title))
        else:
            print(f"  → KEPT (reason: {reason})")

    print(f"\n{'='*60}")
    print(f"RESULTS: {len(changes)} of {len(data['articles'])} titles rewritten\n")
    for aid, old, new in changes:
        print(f"  OLD: {old[:80]}")
        print(f"  NEW: {new}")
        print()

    if changes:
        data['lastUpdated'] = datetime.now(timezone.utc).isoformat() if True else data['lastUpdated']
        from datetime import datetime, timezone
        data['lastUpdated'] = datetime.now(timezone.utc).isoformat()
        with open(ARTICLES_FILE, 'w') as f:
            json.dump(data, f, indent=2)
        print(f"✅ Saved {len(changes)} updated titles to articles.json")
    else:
        print("No changes needed.")
