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
else
    log_ok "gh CLI found"
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
# 5. Global git hooks — gitleaks secret scanning
# ---------------------------------------------------------------------------

if cmd_exists gitleaks; then
    hooks_dir="$HOME/.git-hooks"
    mkdir -p "$hooks_dir"
    cp "$REPO_ROOT/dev/git/pre-commit" "$hooks_dir/pre-commit"
    chmod 755 "$hooks_dir/pre-commit"
    git config --global core.hooksPath "$hooks_dir"
    log_ok "global git hooks deployed to $hooks_dir"
    # Machine-wide gitleaks policy: the hook pins -c at this shared path,
    # so one versioned config covers every repo (incl. the gopass store).
    # Repo .gitleaks.toml is the source of truth; this copy is derived.
    mkdir -p "$HOME/.config/gitleaks"
    cp "$REPO_ROOT/.gitleaks.toml" "$HOME/.config/gitleaks/gitleaks.toml"
    log_ok "shared gitleaks config deployed to $HOME/.config/gitleaks/gitleaks.toml"
else
    log_warn "gitleaks not found — skipping global hooks setup"
fi

# ---------------------------------------------------------------------------
# 6. Global gitignore — personal plans/ folder
# ---------------------------------------------------------------------------

mkdir -p "$HOME/.config/git"
cp "$REPO_ROOT/dev/git/ignore" "$HOME/.config/git/ignore"
log_ok "global gitignore deployed to $HOME/.config/git/ignore"

# ---------------------------------------------------------------------------
# 7. Gtr installation + global defaults
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
