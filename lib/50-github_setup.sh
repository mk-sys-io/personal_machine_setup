#!/usr/bin/env bash
set -euo pipefail

# ---------------------------------------------------------------------------
# 50-github_setup.sh — GitHub CLI + git config
#
# Authenticates gh CLI and configures git global identity.
# Strict (Phase 12-B): token is required from the gopass store
# (services/github key) via lib/helpers/gopass.sh; identity is required from
# committed config.txt. Missing prerequisites fail loud, exit 1.
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
# 1. gh auth login (token required from gopass — no empty-skip, no fallback)
# ---------------------------------------------------------------------------

GITHUB_TOKEN=$("$SCRIPT_DIR/helpers/gopass.sh" services/github key)
if [[ "$(gh auth token 2>/dev/null)" == "$GITHUB_TOKEN" ]]; then
    log_ok "gh already authenticated"
else
    log_step "GitHub authentication"
    echo "$GITHUB_TOKEN" | gh auth login --with-token
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
# 6. Gtr installation + global defaults
# ---------------------------------------------------------------------------

GTR_BIN="/usr/local/bin/git-gtr"
GTR_REPO="$HOME/.local/share/gtr"
GTR_TARGET="$GTR_REPO/bin/git-gtr"

if [[ -L "$GTR_BIN" ]] && [[ -x "$GTR_TARGET" ]] && [[ "$(readlink "$GTR_BIN")" == "$GTR_TARGET" ]]; then
    log_ok "gtr already installed"
else
    log "Installing gtr..."
    rm -rf "$GTR_REPO"
    mkdir -p "$(dirname "$GTR_REPO")"
    retry 3 git clone --depth 1 https://github.com/coderabbitai/git-worktree-runner "$GTR_REPO"
    bash "$GTR_REPO/install.sh"
fi

git config --global gtr.ai.default "${GTR_AI_DEFAULT:-opencode}"
git config --global gtr.editor.default "${GTR_EDITOR_DEFAULT:-zed -r}"
log_ok "gtr global defaults set (ai=${GTR_AI_DEFAULT:-opencode}, editor=${GTR_EDITOR_DEFAULT:-zed -r})"

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

log_step "GitHub setup complete"
exit 0
