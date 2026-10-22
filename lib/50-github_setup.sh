#!/usr/bin/env bash
set -euo pipefail

# ---------------------------------------------------------------------------
# 50-github_setup.sh — GitHub CLI + git config
#
# Authenticates gh CLI and configures git global identity.
# Strict-but-lenient (gopass-removal rework): token comes from an optional
# GITHUB_TOKEN env or a /dev/tty prompt (Bitwarden is the authoritative
# source); empty/declined/headless -> WARN + continue unauthenticated.
# A supplied-but-invalid token (gh 401) fails loud: silently continuing
# unauthenticated would lie about a token the operator believes landed.
# Pasted tokens are scrubbed (bracketed-paste markers, surrounding
# whitespace) and reported as `accepted (N chars)` — never the value.
# Identity is required from committed config.txt. Missing identity fails
# loud, exit 1.
#
# Global git workflow files (pre-commit hook + core.hooksPath, global
# gitignore, gitleaks policy/template) are NOT deployed here — they are
# dev/git/* copies owned by `make dev`. See §5.
# Exit 0 = pass, exit 1 = prerequisite missing
# ---------------------------------------------------------------------------

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/helpers/common.sh"

# ---------------------------------------------------------------------------
# Prerequisite: gh CLI
# ---------------------------------------------------------------------------

if ! cmd_exists gh; then
    log_error "gh CLI not found — install it first (apt install gh)"
    exit 1
else
    log_ok "gh CLI found"
fi

# ---------------------------------------------------------------------------
# 1. gh auth login (token from env or /dev/tty prompt; empty = warn + skip)
# ---------------------------------------------------------------------------

GITHUB_TOKEN="${GITHUB_TOKEN:-}"
if [[ -z "$GITHUB_TOKEN" && -r /dev/tty ]]; then
    printf 'GitHub token (Bitwarden vault): ' > /dev/tty
    IFS= read -r GITHUB_TOKEN < /dev/tty || GITHUB_TOKEN=""
fi
# Scrub pasted-token noise: bracketed-paste markers (kitty/other terminals
# wrap Ctrl+Shift+V in ESC[200~ ... ESC[201~ when the mode is on) and any
# surrounding whitespace CR/LF/tab/space that Bitwarden or the terminal
# may add. Bash leaves bracketed paste disabled while children run, so
# markers are uncommon — scrub anyway; a stray marker is a guaranteed 401.
GITHUB_TOKEN="${GITHUB_TOKEN//$'\e[200~'/}"
GITHUB_TOKEN="${GITHUB_TOKEN//$'\e[201~'/}"
GITHUB_TOKEN="${GITHUB_TOKEN//$'[\e\r]'/}"
GITHUB_TOKEN="${GITHUB_TOKEN#"${GITHUB_TOKEN%%[![:space:]]*}"}"
GITHUB_TOKEN="${GITHUB_TOKEN%"${GITHUB_TOKEN##*[![:space:]]}"}"
if [[ -z "$GITHUB_TOKEN" ]]; then
    log_warn "no token (declined or headless) — continuing unauthenticated; run 'gh auth login' later"
elif [[ "$(gh auth token 2>/dev/null)" == "$GITHUB_TOKEN" ]]; then
    log_ok "gh already authenticated"
else
    log_step "GitHub authentication"
    if [[ -w /dev/tty ]]; then
        printf 'accepted (%d chars)\n' "${#GITHUB_TOKEN}" > /dev/tty
    else
        log_ok "token accepted (${#GITHUB_TOKEN} chars)"
    fi
    if ! printf '%s\n' "$GITHUB_TOKEN" | gh auth login --with-token; then
        log_error "GitHub rejected the token (401 Bad credentials) —"
        log_error "check the Bitwarden entry (expired? revoked? wrong field?),"
        log_error "copy a FRESH token, then re-run: ./install.py --only 50-github"
        exit 1
    fi
    log_ok "gh authenticated"
fi
unset GITHUB_TOKEN

# ---------------------------------------------------------------------------
# 2. git config user.name (required from committed config.txt)
# ---------------------------------------------------------------------------

if [[ -z "${GIT_USER_NAME:-}" ]]; then
    log_error "GIT_USER_NAME is empty — set it in committed config.txt"
    exit 1
else
    git config --global user.name "$GIT_USER_NAME"
    log_ok "git config user.name set"
fi

# ---------------------------------------------------------------------------
# 3. git config user.email (required from committed config.txt)
# ---------------------------------------------------------------------------

if [[ -z "${GIT_USER_EMAIL:-}" ]]; then
    log_error "GIT_USER_EMAIL is empty — set it in committed config.txt"
    exit 1
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
# 5. Global git workflow — moved to `make dev`
# ---------------------------------------------------------------------------
#
# Was: the pre-commit hook + core.hooksPath, ~/.config/git/ignore, and the
# machine-wide gitleaks policy. All are now verbatim dev/git/* copies made by
# `make dev`, which needs neither sudo nor network:
#
#   dev/git/ignore        -> ~/.config/git/ignore
#   dev/git/pre-commit    -> ~/.git-hooks/pre-commit  + core.hooksPath
#   dev/git/gitleaks.toml -> ~/.config/gitleaks/gitleaks.toml  (machine-wide)
#                        -> ~/.config/init/gitleaks.toml      (init discovery)
#
# dev/git/gitleaks.toml is the deployed source for both gitleaks copies; the
# repo-root .gitleaks.toml is a local-only fork and is never deployed.
#
# Known gap (accepted): `./install.py --only 50-github` therefore deploys no
# git workflow files at all — pair it with `make dev`.

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

log_step "GitHub setup complete"
exit 0
