#!/usr/bin/env bash
set -euo pipefail

# ---------------------------------------------------------------------------
# 05-home-skeleton.sh — Create user-visible home folders + XDG user-dirs
#
# Called from install.py only (install.sh MODULES is frozen — do not add).
# Position: after ensure_stage0 + load_env (12-unified-config) and
#   20-packages (packages-first: provides xdg-user-dirs-update), before
#   40-restore_home (staged restore needs targets) and 30
#   user_dotfiles/user_dev converge. Numeric slot mirrors blank-iron run
#   position (see 00-index.md).
#
# Flow: guard HOME non-empty → guard xdg-user-dirs-update present →
#   mkdir -p -m 755 the 7 owned dirs (15 §15.1) → xdg-user-dirs-update →
#   one-time legacy ~/Screenshots migration (15 §15.3 part 2).
# Never writes keep.manifest (Option B — owned by the backup pipeline,
#   40 §40.2). Never --delete. Never touches /etc/xdg/* (core side).
#
# Exit contract (0=pass, 1=fail, 2=skip):
#   0  converged now, or already converged (idempotent re-run)
#   1  HOME unset — refusing to mkdir near filesystem root
#   2  xdg-user-dirs-update missing — packages-first gap (covers
#      --skip 20 runs; the binary is guaranteed by the runner order)
# ---------------------------------------------------------------------------

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common.sh"

# Belt-and-braces for standalone runs (install.sh creates this too).
mkdir -p "$LOG_DIR"

# ---------------------------------------------------------------------------
# 0. Preconditions
# ---------------------------------------------------------------------------

if [[ -z "${HOME:-}" ]]; then
    log_error "HOME is unset — refusing to mkdir near filesystem root."
    exit 1
fi

if ! cmd_exists xdg-user-dirs-update; then
    log_warn "xdg-user-dirs-update missing — 20-packages has not converged (packages-first gap)."
    exit 2
fi

# ---------------------------------------------------------------------------
# 1. Owned dir set (15 §15.1) — mkdir only, never --delete
# ---------------------------------------------------------------------------

mkdir -p -m 755 \
    "$HOME/Downloads" \
    "$HOME/Documents" \
    "$HOME/Music" \
    "$HOME/Pictures" \
    "$HOME/Pictures/Screenshots" \
    "$HOME/Videos" \
    "$HOME/Videos/keep"
# NOTE (SC2174, accepted): with -p, -m applies only to the deepest
# directory — safe here by construction because every skeleton dir is an
# explicit argument; the sole implicit parent ($HOME) pre-exists.
log "Skeleton dirs present (mkdir -p -m 755, idempotent)."
# NOTE: keep.manifest is NEVER written here (Option B, D9).

# ---------------------------------------------------------------------------
# 2. XDG user-dirs (15 §15.2) — pinned file deployed by `make dotfiles`
# ---------------------------------------------------------------------------

xdg-user-dirs-update
log_ok "xdg-user-dirs-update converged."
# Do NOT touch /etc/xdg/user-dirs.defaults or /etc/xdg/user-dirs.conf.

# ---------------------------------------------------------------------------
# 3. One-time legacy screenshot migration (15 §15.3 part 2, reversible)
# ---------------------------------------------------------------------------

LEGACY_SCREENSHOTS="$HOME/Screenshots"
SCREENSHOT_TARGET="$HOME/Pictures/Screenshots"
if [[ -d "$LEGACY_SCREENSHOTS" ]]; then
    # Empty dir: glob stays literal, mv fails, || true absorbs it.
    # rmdir only succeeds when empty — safe by construction.
    mv "$LEGACY_SCREENSHOTS"/* "$SCREENSHOT_TARGET"/ 2>/dev/null || true
    if rmdir "$LEGACY_SCREENSHOTS" 2>/dev/null; then
        log_ok "Migrated legacy $LEGACY_SCREENSHOTS into $SCREENSHOT_TARGET."
    else
        log_warn "Legacy $LEGACY_SCREENSHOTS not empty after move — left in place."
    fi
else
    log "No legacy $LEGACY_SCREENSHOTS — nothing to migrate."
fi

log_ok "home_skeleton converged."
exit 0
