#!/usr/bin/env bash
# ============================================================
# The Mold Report — Session Bootstrap
# ============================================================
# This script ensures the repo and environment are ready.
# Designed to be called at the start of every scheduled task.
#
# Usage:
#   source /tmp/the-mold-report/bootstrap.sh
#   — or if repo doesn't exist yet —
#   Call bootstrap_repo() first, then source this.
#
# What it does:
#   1. Clones the repo if /tmp/the-mold-report doesn't exist
#   2. Writes .env from arguments if missing
#   3. Installs Python dependencies
#   4. Exports REPO_DIR for downstream scripts
# ============================================================

set -euo pipefail

REPO_DIR="/tmp/the-mold-report"
ENV_FILE="${REPO_DIR}/.env"
GITHUB_REPO_URL_BASE="https://github.com/jlpxmoldco/the-mold-report.git"

# ── Step 1: Clone repo if needed ──────────────────────────────
bootstrap_repo() {
    local github_token="${1:-}"

    if [ -d "${REPO_DIR}/.git" ]; then
        echo "✓ Repo exists at ${REPO_DIR}"
        cd "${REPO_DIR}"
        # Pull latest changes
        if [ -n "${github_token}" ]; then
            local auth_url="https://x-access-token:${github_token}@github.com/jlpxmoldco/the-mold-report.git"
            git remote set-url origin "${auth_url}" 2>/dev/null || true
        fi
        git pull --ff-only 2>/dev/null || echo "  ⚠ Pull failed (may have local changes)"
    else
        echo "→ Cloning repo to ${REPO_DIR}..."
        if [ -n "${github_token}" ]; then
            local auth_url="https://x-access-token:${github_token}@github.com/jlpxmoldco/the-mold-report.git"
            git clone "${auth_url}" "${REPO_DIR}" 2>&1
        else
            git clone "${GITHUB_REPO_URL_BASE}" "${REPO_DIR}" 2>&1
        fi
        cd "${REPO_DIR}"
    fi

    # Always set git identity for commits
    git config user.email "bot@themoldreport.com"
    git config user.name "Mold Report Bot"

    echo "✓ Repo ready at ${REPO_DIR}"
}

# ── Step 2: Write .env if missing ─────────────────────────────
write_env() {
    # Accepts key=value pairs as arguments
    if [ -f "${ENV_FILE}" ] && [ -s "${ENV_FILE}" ]; then
        echo "✓ .env exists ($(wc -l < "${ENV_FILE}") lines)"
        return 0
    fi

    echo "→ Writing .env..."
    > "${ENV_FILE}"  # Create/truncate
    for pair in "$@"; do
        echo "${pair}" >> "${ENV_FILE}"
    done
    echo "✓ .env written ($(wc -l < "${ENV_FILE}") lines)"
}

# ── Step 3: Install Python deps ───────────────────────────────
install_deps() {
    echo "→ Installing Python dependencies..."
    pip install anthropic feedparser requests beautifulsoup4 --break-system-packages -q 2>&1 | tail -1
    echo "✓ Dependencies installed"
}

# ── Step 4: Verify environment ────────────────────────────────
verify_env() {
    local errors=0

    if [ ! -f "${ENV_FILE}" ]; then
        echo "✗ FATAL: .env missing at ${ENV_FILE}"
        return 1
    fi

    # Check required keys exist and are non-empty
    for key in ANTHROPIC_API_KEY GITHUB_TOKEN GITHUB_REPO_URL; do
        local val=$(grep "^${key}=" "${ENV_FILE}" | cut -d= -f2-)
        if [ -z "${val}" ]; then
            echo "✗ FATAL: ${key} is missing or empty in .env"
            errors=$((errors + 1))
        fi
    done

    # Check at least one RSS feed
    local rss_count=$(grep -c "^MOLD_REPORT_RSS_" "${ENV_FILE}" 2>/dev/null || echo 0)
    if [ "${rss_count}" -eq 0 ]; then
        echo "⚠ WARNING: No RSS feeds configured"
    else
        echo "✓ ${rss_count} RSS feeds configured"
    fi

    if [ "${errors}" -gt 0 ]; then
        echo "✗ Environment verification FAILED (${errors} errors)"
        return 1
    fi

    echo "✓ Environment verified"
    return 0
}

# ── Full bootstrap (call all steps) ──────────────────────────
full_bootstrap() {
    # Args: github_token env_key1=val1 env_key2=val2 ...
    local github_token="${1:-}"
    shift || true

    bootstrap_repo "${github_token}"
    write_env "$@"
    install_deps
    verify_env

    export REPO_DIR
    echo ""
    echo "============================================================"
    echo "  ✓ Bootstrap complete. Working directory: ${REPO_DIR}"
    echo "============================================================"
    echo ""
}
