#!/usr/bin/env bash
set -euo pipefail

# ---------------------------------------------------------------------------
# 05-home-skeleton.sh — Create user-visible home folders + XDG user-dirs
#
# Called from install.py only.
# Position: after ensure_stage0 + load_env (12-unified-config) and
#   20-packages (packages-first: provides xdg-user-dirs-update), before
#   40-restore_home (staged restore needs targets) and 30
#   user_dotfiles/user_dev converge. Numeric slot mirrors blank-iron run
#   position (see 00-index.md).
#
# Flow: guard HOME non-empty → guard xdg-user-dirs-update present →
#   mkdir -p -m 755 the 7 owned dirs (15 §15.1) → xdg-user-dirs-update.
# (Legacy ~/Screenshots migration executed 2026-09-24 and retired —
#   see 15 §15.3 part 2; this step is now mkdir + xdg-update only.)
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
source "$SCRIPT_DIR/helpers/common.sh"

# Belt-and-braces for standalone runs (the orchestrator creates this too).
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

log_ok "home_skeleton converged."
exit 0
