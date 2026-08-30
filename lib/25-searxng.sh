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
LIMITER_DEST="$SEARXNG_DIR/searx/limiter.toml"

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

# SearXNG hard-refuses to start (sys.exit(1) in searx/webapp.py) if
# server.secret_key is unset or still the placeholder "ultrasecretkey" — the
# key is used for cryptography (session signing/HMAC), so a known public
# default is a security hole. The repo settings file deliberately ships no
# secret (never commit credentials; gitleaks would flag it), so we generate
# one here at deploy time. Only inject if missing so re-runs don't rotate the
# key (which would invalidate existing sessions).
if ! grep -qE '^[[:space:]]+secret_key:' "$SETTINGS_DEST"; then
    # Prefer openssl; fall back to the venv python's stdlib `secrets` if
    # openssl is missing or fails (no entropy, old version, empty output).
    SECRET_KEY="$(openssl rand -hex 32 2>/dev/null)" || true
    if [[ -z "$SECRET_KEY" ]]; then
        SECRET_KEY="$("$VENV_PY" -c 'import secrets; print(secrets.token_hex(32))')"
    fi
    awk -v key="$SECRET_KEY" '/^server:/ { print; print "  secret_key: " key; next } { print }' \
        "$SETTINGS_DEST" > "$SETTINGS_DEST.tmp" && mv "$SETTINGS_DEST.tmp" "$SETTINGS_DEST"
    log_ok "Generated server.secret_key"
fi

log_step "Deploying limiter config"
cp "$REPO_ROOT/services/search/limiter.toml" "$LIMITER_DEST"

# Inject the repo's dark-only modern theme CSS into the stock simple theme.
# The whole modern layout is a CSS override on the stock markup, so appending
# it to the theme stylesheet is enough. Idempotent: any previous injection is
# removed (marker-delimited) before re-appending, and re-applied after a
# `git pull` resets the CSS.
log_step "Injecting modern dark theme CSS"
MODERN_CSS="$REPO_ROOT/services/search/searxng-modern-dark.css"
for css in "$SEARXNG_DIR/searx/static/themes/simple/sxng-ltr.min.css" \
           "$SEARXNG_DIR/searx/static/themes/simple/sxng-rtl.min.css"; do
    if [[ -f "$css" ]]; then
        sed -i '/\/\* searxng-modern:start \*\//,/\/\* searxng-modern:end \*\//d' "$css"
        {
            printf '\n/* searxng-modern:start */\n'
            cat "$MODERN_CSS"
            printf '\n/* searxng-modern:end */\n'
        } >> "$css"
        log_ok "Injected modern theme into $(basename "$css")"
    fi
done

log_step "Deploying systemd user unit"
mkdir -p "$(dirname "$UNIT_DEST")"
cp "$REPO_ROOT/services/search/searxng.service" "$UNIT_DEST"

# ---------------------------------------------------------------------------
# 3. Enable + start
# ---------------------------------------------------------------------------

systemctl --user daemon-reload
systemctl --user enable searxng
# `restart` (not `enable --now`) so WhiteNoise re-indexes the static tree and
# picks up the injected theme CSS on every run.
systemctl --user restart searxng

log_ok "SearXNG installed and enabled"
exit 0
