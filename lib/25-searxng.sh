#!/usr/bin/env bash
set -euo pipefail

# ---------------------------------------------------------------------------
# 25-searxng.sh — SearXNG install (user-space venv + systemd user unit)
#
# Installs SearXNG from source into a user-space Python venv at ~/searxng and
# runs it under a systemd USER unit (no sudo, no container, no uWSGI). The
# repo's services/search/ files are the source of truth and are copied to
# their live destinations here — this repo is a mirror of the live system.
#
# Idempotent: guarded by `test -x ~/searxng/venv/bin/python`, so re-runs skip
# the clone/venv/pip steps but always re-apply the settings + unit + enable.
#
# Exit codes: 0=pass, 1=fail, 2=skip (per module contract).
# ---------------------------------------------------------------------------

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common.sh"

SEARXNG_DIR="$REAL_HOME/searxng"
VENV_PY="$SEARXNG_DIR/venv/bin/python"
UNIT_DEST="$REAL_HOME/.config/systemd/user/searxng.service"
SETTINGS_DEST="$SEARXNG_DIR/searxng/settings.yml"

log_step "SearXNG install"

# ---------------------------------------------------------------------------
# 1. Clone + venv + pip install (idempotent)
# ---------------------------------------------------------------------------

if [[ -x "$VENV_PY" ]]; then
    log_ok "SearXNG venv already present — skipping clone/venv/pip"
else
    log_step "Cloning SearXNG source"
    git clone --depth 1 https://github.com/searxng/searxng "$SEARXNG_DIR"

    log_step "Creating Python venv"
    python3 -m venv "$SEARXNG_DIR/venv"

    log_step "Installing SearXNG into venv"
    # SearXNG has no pyproject.toml; its setup.py imports searx (needs
    # pyyaml/msgspec) at build time. With build isolation ON, pip's isolated
    # env has only setuptools, so the build fails with ModuleNotFoundError.
    # Pre-install build deps into the venv, then build with --no-build-isolation
    # (the official SearXNG install method).
    "$VENV_PY" -m pip install -U pip setuptools wheel pyyaml msgspec typing-extensions pybind11
    "$VENV_PY" -m pip install --use-pep517 --no-build-isolation -e "$SEARXNG_DIR"
fi

# ---------------------------------------------------------------------------
# 2. Deploy repo-managed settings + unit (always re-applied)
# ---------------------------------------------------------------------------

log_step "Deploying SearXNG settings"
mkdir -p "$(dirname "$SETTINGS_DEST")"
cp "$REPO_ROOT/services/search/searxng-settings.yml" "$SETTINGS_DEST"

log_step "Deploying systemd user unit"
mkdir -p "$(dirname "$UNIT_DEST")"
cp "$REPO_ROOT/services/search/searxng.service" "$UNIT_DEST"

# ---------------------------------------------------------------------------
# 3. Enable + start
# ---------------------------------------------------------------------------

systemctl --user daemon-reload
systemctl --user enable --now searxng

log_ok "SearXNG installed and enabled"
exit 0
