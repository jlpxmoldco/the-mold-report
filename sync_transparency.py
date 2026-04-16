#!/usr/bin/env python3
"""
sync_transparency.py — Auto-sync pipeline config → site transparency section

Reads pipeline_config.json and regenerates the following sections in index.html:
  1. Pipeline visual steps (pipe-step divs)
  2. Weights & Biases cards (wb-card divs)
  3. Version log entries (vl-entry divs)
  4. Agent count in the intro paragraph

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
    """
    Generic replacer: finds SYNC markers and replaces content between them.
    If markers don't exist yet, finds old content by class name and injects markers.
    """
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

def generate_pipe_steps(config):
    lines = []
    for gate in config["gates"]:
        lines.append(f'        <div class="pipe-step">')
        lines.append(f'          <div class="pipe-icon">{gate["icon"]}</div>')
        lines.append(f'          <div>')
        lines.append(f'            <h4>{gate["name"]}</h4>')
        lines.append(f'            <p>{gate["short_desc"]}</p>')
        lines.append(f'          </div>')
        lines.append(f'        </div>')
    return "\n".join(lines)


def generate_wb_cards(config):
    lines = []
    # Static cards first (Model, Source Bias, etc.)
    for card in config.get("static_wb_cards", []):
        lines.append(f'          <div class="wb-card">')
        lines.append(f'            <h4>{card["title"]}</h4>')
        lines.append(f'            <p>{card["desc"]}</p>')
        if "meter_pct" in card:
            lines.append(f'            <div class="wb-meter"><div class="wb-meter-fill" style="width:{card["meter_pct"]}%;background:{card["meter_color"]};"></div></div>')
            lines.append(f'            <p style="margin-top:4px;font-size:11px;">{card["meter_label"]}</p>')
        lines.append(f'          </div>')
    # Gate-specific cards
    for gate in config["gates"]:
        if "wb_title" not in gate:
            continue
        lines.append(f'          <div class="wb-card">')
        lines.append(f'            <h4>{gate["wb_title"]}</h4>')
        lines.append(f'            <p>{gate["wb_desc"]}</p>')
        if "wb_meter_pct" in gate:
            lines.append(f'            <div class="wb-meter"><div class="wb-meter-fill" style="width:{gate["wb_meter_pct"]}%;background:{gate["wb_meter_color"]};"></div></div>')
            lines.append(f'            <p style="margin-top:4px;font-size:11px;">{gate["wb_meter_label"]}</p>')
        lines.append(f'          </div>')
    return "\n".join(lines)


def generate_version_log(config):
    lines = []
    for entry in config["version_log"]:
        lines.append(f'        <div class="vl-entry">')
        lines.append(f'          <span class="vl-version">{entry["version"]}</span>')
        lines.append(f'          <div class="vl-body">')
        lines.append(f'            <strong>{entry["title"]}</strong>')
        lines.append(f'            <span>{entry["date"]}</span>')
        lines.append(f'            <p>{entry["desc"]}</p>')
        lines.append(f'          </div>')
        lines.append(f'        </div>')
    return "\n".join(lines)


def update_agent_count(html, config):
    count = config["agent_count"]
    words = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five",
             6: "six", 7: "seven", 8: "eight", 9: "nine", 10: "ten"}
    word = words.get(count, str(count))
    new_html, n = re.subn(r'(\w+)\s+AI sub-agents', f'{word} AI sub-agents', html)
    if n > 0:
        print(f"  ✓ Updated agent count to '{word}'")
    return new_html


# =========================================
# FIRST-TIME MARKER INJECTION
# =========================================

def find_block_end(html, start_pos, block_class):
    """Find all consecutive blocks of a given class and return the end position."""
    pos = start_pos
    last_end = start_pos
    while True:
        next_block = html.find(f'<div class="{block_class}">', pos + 1)
        if next_block == -1 or next_block > start_pos + 15000:
            break
        pos = next_block
    # Find closing </div> of the last block (count div depth)
    depth = 0
    i = pos
    while i < len(html):
        if html[i:i+4] == '<div':
            depth += 1
        elif html[i:i+6] == '</div>':
            depth -= 1
            if depth == 0:
                return i + 6
        i += 1
    return last_end


def inject_markers_if_missing(html):
    """First-time setup: wrap existing content with sync markers."""
    changed = False

    # Pipe steps
    if 'SYNC:PIPE_STEPS_START' not in html:
        first = html.find('<div class="pipe-step">')
        if first != -1:
            end = find_block_end(html, first, "pipe-step")
            marker_s = '        <!-- SYNC:PIPE_STEPS_START -->\n'
            marker_e = '\n        <!-- SYNC:PIPE_STEPS_END -->'
            html = html[:first] + marker_s + html[first:end] + marker_e + html[end:]
            print("  ✓ Injected pipe-steps markers (first run)")
            changed = True

    # WB cards
    if 'SYNC:WB_CARDS_START' not in html:
        first = html.find('<div class="wb-card">')
        if first != -1:
            end = find_block_end(html, first, "wb-card")
            marker_s = '          <!-- SYNC:WB_CARDS_START -->\n'
            marker_e = '\n          <!-- SYNC:WB_CARDS_END -->'
            html = html[:first] + marker_s + html[first:end] + marker_e + html[end:]
            print("  ✓ Injected wb-cards markers (first run)")
            changed = True

    # Version log
    if 'SYNC:VERSION_LOG_START' not in html:
        first = html.find('<div class="vl-entry">')
        if first != -1:
            end = find_block_end(html, first, "vl-entry")
            marker_s = '        <!-- SYNC:VERSION_LOG_START -->\n'
            marker_e = '\n        <!-- SYNC:VERSION_LOG_END -->'
            html = html[:first] + marker_s + html[first:end] + marker_e + html[end:]
            print("  ✓ Injected version-log markers (first run)")
            changed = True

    return html, changed


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

    print(f"\n→ Pipeline v{config['version']} | {len(config['gates'])} gates | {config['agent_count']} agents\n")

    # Step 1: Ensure markers exist
    html, markers_added = inject_markers_if_missing(html)

    # Step 2: Replace content between markers
    html = replace_between_markers(html, "SYNC:PIPE_STEPS_START", "SYNC:PIPE_STEPS_END",
                                   generate_pipe_steps(config), f"pipe-steps ({len(config['gates'])} gates)")
    html = replace_between_markers(html, "SYNC:WB_CARDS_START", "SYNC:WB_CARDS_END",
                                   generate_wb_cards(config), f"wb-cards")
    html = replace_between_markers(html, "SYNC:VERSION_LOG_START", "SYNC:VERSION_LOG_END",
                                   generate_version_log(config), f"version-log ({len(config['version_log'])} entries)")
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
