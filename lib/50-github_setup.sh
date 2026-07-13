#!/usr/bin/env bash
set -euo pipefail

# ---------------------------------------------------------------------------
# 50-github_setup.sh — GitHub CLI + git config
#
# Authenticates gh CLI and configures git global identity.
# Degrades gracefully if credentials are empty (orchestrator already warned).
# Exit 0 = pass, exit 1 = prerequisite missing
# ---------------------------------------------------------------------------

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common.sh"

# ---------------------------------------------------------------------------
# Prerequisite: gh CLI
# ---------------------------------------------------------------------------

if ! cmd_exists gh; then
    log_error "gh CLI not found — install it first (apt install gh)"
    exit 1
fi

# ---------------------------------------------------------------------------
# 1. gh auth login (skip if token empty)
# ---------------------------------------------------------------------------

if [[ -z "${GITHUB_TOKEN:-}" ]]; then
    log_warn "GITHUB_TOKEN is empty — skipping gh auth login"
elif [[ "$(gh auth token 2>/dev/null)" == "$GITHUB_TOKEN" ]]; then
    log_ok "gh already authenticated"
else
    log_step "GitHub authentication"
    echo "$GITHUB_TOKEN" | gh auth login --with-token
    log_ok "gh authenticated"
fi

# ---------------------------------------------------------------------------
# 2. git config user.name (skip if empty)
# ---------------------------------------------------------------------------

if [[ -z "${GIT_USER_NAME:-}" ]]; then
    log_warn "GIT_USER_NAME is empty — skipping git config user.name"
else
    git config --global user.name "$GIT_USER_NAME"
    log_ok "git config user.name set"
fi

# ---------------------------------------------------------------------------
# 3. git config user.email (skip if empty)
# ---------------------------------------------------------------------------

if [[ -z "${GIT_USER_EMAIL:-}" ]]; then
    log_warn "GIT_USER_EMAIL is empty — skipping git config user.email"
else
    git config --global user.email "$GIT_USER_EMAIL"
    log_ok "git config user.email set"
fi

# ---------------------------------------------------------------------------
# 4. git config credential.helper (always set)
# ---------------------------------------------------------------------------

git config --global credential.helper "!gh auth git-credential"
log_ok "git config credential.helper set"

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

log_step "GitHub setup complete"
exit 0
