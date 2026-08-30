#!/usr/bin/env bash
set -euo pipefail

# ---------------------------------------------------------------------------
# brave-merge-preferences.sh — Merge the Brave NTP preferences seed into the
# live profile Preferences file.
#
# User-level dotfile deploy step (invoked from `make dotfiles` or run singly).
# Brave writes Preferences on exit, so a merge while Brave is running would be
# silently overwritten — this module terminates Brave gracefully first
# (SIGTERM → poll for full exit → SIGKILL stragglers), then merges.
#
# Exit codes: 0 = merged/skipped, 1 = failure.
# ---------------------------------------------------------------------------

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

SEED="$REPO_ROOT/dotfiles/browsers/brave/preferences.seed.json"
PREFS="$HOME/.config/BraveSoftware/Brave-Browser/Default/Preferences"

# 1. Skip if no profile yet (fresh machine, Brave never run)
if [[ ! -f "$PREFS" ]]; then
    echo "brave-preferences: no Preferences file at $PREFS — skipping"
    exit 0
fi

# 2. Fail closed if the seed is missing (repo is source of truth)
if [[ ! -f "$SEED" ]]; then
    echo "brave-preferences: ERROR seed missing: $SEED" >&2
    exit 1
fi

# 3. Kill-then-wait: graceful SIGTERM, poll for full exit, SIGKILL stragglers
if pgrep -x brave >/dev/null 2>&1; then
    echo "brave-preferences: Brave running — terminating gracefully"
    pkill -TERM -x brave || true
    for _ in $(seq 1 20); do
        pgrep -x brave >/dev/null 2>&1 || break
        sleep 0.5
    done
    if pgrep -x brave >/dev/null 2>&1; then
        echo "brave-preferences: Brave did not exit — forcing kill"
        pkill -KILL -x brave || true
        sleep 1
    fi
fi

# 4. Backup
backup="$PREFS.bak-$(date +%s)"
cp "$PREFS" "$backup"
echo "brave-preferences: backed up to $backup"

# 5. Deep-merge seed into Preferences (atomic write, preserve permissions)
python3 - "$SEED" "$PREFS" <<'PY'
import json
import os
import sys

seed_path, prefs_path = sys.argv[1], sys.argv[2]

with open(seed_path) as f:
    seed = json.load(f)
with open(prefs_path) as f:
    prefs = json.load(f)


def deep_merge(base, overlay):
    for k, v in overlay.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            deep_merge(base[k], v)
        else:
            base[k] = v


deep_merge(prefs, seed)

tmp = prefs_path + ".tmp"
with open(tmp, "w") as f:
    json.dump(prefs, f, indent=2)
    f.write("\n")
os.chmod(tmp, os.stat(prefs_path).st_mode)
os.replace(tmp, prefs_path)
PY

# 6. Verify seed keys present with correct values
python3 - "$SEED" "$PREFS" <<'PY'
import json
import sys

seed_path, prefs_path = sys.argv[1], sys.argv[2]
with open(seed_path) as f:
    seed = json.load(f)
with open(prefs_path) as f:
    prefs = json.load(f)


def walk(base, overlay, path=""):
    for k, v in overlay.items():
        p = f"{path}.{k}" if path else k
        if isinstance(v, dict):
            if not isinstance(base.get(k), dict):
                print(f"brave-preferences: ERROR {p} missing or wrong type", file=sys.stderr)
                sys.exit(1)
            walk(base[k], v, p)
        elif base.get(k) != v:
            print(f"brave-preferences: ERROR {p} = {base.get(k)!r}, expected {v!r}", file=sys.stderr)
            sys.exit(1)


walk(prefs, seed)
PY

echo "brave-preferences: merged and verified"
exit 0