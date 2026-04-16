#!/usr/bin/env python3
"""
sync_transparency.py — Auto-sync pipeline config → site transparency section

Reads pipeline_config.json and regenerates the following sections in index.html:
  1. Phase cards (pipeline-phases div)
  2. Gate list (gate-item divs inside collapsible)
  3. Weights & Biases cards (wb-card divs, static only)
  4. Version log entries (vl-entry divs)
  5. Agent count in the intro paragraph

Usage:
  python sync_transparency.py              # Update index.html in place
  python sync_transparency.py --dry-run    # Preview changes without writing

This script is safe to run repeatedly — it uses HTML comment markers to find
and replace only the managed sections.
"""

import json
import sys
import re
import os
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(SCRIPT_DIR, "pipeline_config.json")
INDEX_PATH = os.path.join(SCRIPT_DIR, "index.html")


def load_config():
    with open(CONFIG_PATH) as f:
        return json.load(f)


def load_index():
    with open(INDEX_PATH) as f:
        return f.read()


def save_index(html):
    with open(INDEX_PATH, "w") as f:
        f.write(html)


def replace_between_markers(html, start_marker, end_marker, new_content, label):
    pattern = re.compile(
        rf'([ \t]*<!--\s*{re.escape(start_marker)}\s*-->).*?(<!--\s*{re.escape(end_marker)}\s*-->)',
        re.DOTALL
    )
    match = pattern.search(html)
    if match:
        replacement = f'{match.group(1)}\n{new_content}\n{match.group(2)}'
        html = html[:match.start()] + replacement + html[match.end():]
        print(f"  ✓ Updated {label}")
        return html
    else:
        print(f"  ⚠ Markers {start_marker} not found — run first-time setup")
        return html


# =========================================
# GENERATORS
# =========================================

def generate_phases(config):
    lines = []
    phases = config.get("phases", [])
    for i, phase in enumerate(phases):
        lines.append(f'      <div class="phase-card">')
        lines.append(f'        <div class="phase-num">Phase {phase["num"]}</div>')
        lines.append(f'        <h4>{phase["name"]}</h4>')
        lines.append(f'        <p>{phase["desc"]}</p>')
        lines.append(f'        <span class="phase-count">{len(phase["gate_ids"])} gate{"s" if len(phase["gate_ids"]) != 1 else ""}</span>')
        lines.append(f'      </div>')
        if i < len(phases) - 1:
            lines.append(f'      <div class="phase-arrow">&rarr;</div>')
    return "\n".join(lines)


def generate_gate_list(config):
    lines = []
    lines.append('          <div class="gate-list">')
    for i, gate in enumerate(config["gates"], 1):
        desc = gate["short_desc"].replace("→", ",")
        lines.append(f'            <div class="gate-item"><div class="gate-icon">{gate["icon"]}</div><div class="gate-info"><h5>{i}. {gate["name"]}</h5><p>{desc}</p></div></div>')
    lines.append('          </div>')
    return "\n".join(lines)


def generate_wb_cards(config):
    lines = []
    # Static cards only (no gate-specific cards in new design)
    for card in config.get("static_wb_cards", []):
        lines.append(f'            <div class="wb-card">')
        lines.append(f'              <h4>{card["title"]}</h4>')
        lines.append(f'              <p>{card["desc"]}</p>')
        if "meter_pct" in card:
            lines.append(f'              <div class="wb-meter"><div class="wb-meter-fill" style="width:{card["meter_pct"]}%;background:{card["meter_color"]};"></div></div>')
            lines.append(f'              <p style="margin-top:4px;font-size:11px;">{card["meter_label"]}</p>')
        lines.append(f'            </div>')
    return "\n".join(lines)


def generate_version_log(config):
    lines = []
    for entry in config["version_log"]:
        lines.append(f'          <div class="vl-entry">')
        lines.append(f'            <span class="vl-version">{entry["version"]}</span>')
        lines.append(f'            <div class="vl-body">')
        lines.append(f'              <strong>{entry["title"]}</strong>')
        lines.append(f'              <span>{entry["date"]}</span>')
        lines.append(f'              <p>{entry["desc"]}</p>')
        lines.append(f'            </div>')
        lines.append(f'          </div>')
    return "\n".join(lines)


def update_agent_count(html, config):
    count = config["agent_count"]
    words = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five",
             6: "six", 7: "seven", 8: "eight", 9: "nine", 10: "ten"}
    word = words.get(count, str(count))
    new_html, n = re.subn(r'(\w+)\s+specialized AI agents', f'{word} specialized AI agents', html)
    if n > 0:
        print(f"  ✓ Updated agent count to '{word}'")
    return new_html


# =========================================
# MAIN
# =========================================

def main():
    dry_run = "--dry-run" in sys.argv

    print("=" * 50)
    print("  sync_transparency.py")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 50)

    config = load_config()
    html = load_index()
    original = html

    print(f"\n→ Pipeline v{config['version']} | {len(config['gates'])} gates | {len(config.get('phases', []))} phases | {config['agent_count']} agents\n")

    # Replace content between markers
    html = replace_between_markers(html, "SYNC:PHASES_START", "SYNC:PHASES_END",
                                   f'      <div class="pipeline-phases">\n{generate_phases(config)}\n      </div>',
                                   f"phases ({len(config.get('phases', []))} phases)")
    html = replace_between_markers(html, "SYNC:GATE_LIST_START", "SYNC:GATE_LIST_END",
                                   generate_gate_list(config),
                                   f"gate-list ({len(config['gates'])} gates)")
    html = replace_between_markers(html, "SYNC:WB_CARDS_START", "SYNC:WB_CARDS_END",
                                   generate_wb_cards(config),
                                   f"wb-cards ({len(config.get('static_wb_cards', []))} cards)")
    html = replace_between_markers(html, "SYNC:VERSION_LOG_START", "SYNC:VERSION_LOG_END",
                                   generate_version_log(config),
                                   f"version-log ({len(config['version_log'])} entries)")
    html = update_agent_count(html, config)

    if html == original:
        print("\n✓ No changes needed — site is already in sync.")
        return

    if dry_run:
        print(f"\n⚠ Dry run — no changes written.")
        return

    save_index(html)
    print(f"\n✓ index.html updated successfully.")


if __name__ == "__main__":
    main()
