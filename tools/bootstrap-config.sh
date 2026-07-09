#!/usr/bin/env bash
set -euo pipefail

# bootstrap-config.sh — Auto-detect system values and generate config.env

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CONFIG_ENV="$REPO_ROOT/config.env"

# ── Detect values ──

detect_username() { whoami; }
detect_uid()      { id -u; }
detect_gid()      { id -g; }

detect_opencode_path() {
    if [ -x "$HOME/.opencode/bin/opencode" ]; then
        echo "$HOME/.opencode"
    else
        echo "$HOME/.opencode"
    fi
}

detect_obsidian_vault() {
    for dir in ~/knowledge_base ~/Obsidian ~/Documents/Obsidian; do
        if [ -d "$dir" ]; then
            echo "$dir"
            return
        fi
    done
    echo ""
}

detect_terminal() {
    update-alternatives --list x-terminal-emulator 2>/dev/null | head -1 || echo ""
}

# ── Gather ──

USERNAME_VAL="$(detect_username)"
UID_VAL="$(detect_uid)"
GID_VAL="$(detect_gid)"
OPENCODE_VAL="$(detect_opencode_path)"
OBSIDIAN_VAL="$(detect_obsidian_vault)"
TERMINAL_VAL="$(detect_terminal)"

# ── Display ──

echo "=== Detected values ==="
echo "USERNAME=$USERNAME_VAL"
echo "USER_UID=$UID_VAL"
echo "USER_GID=$GID_VAL"
echo "OPENCODE_PATH=$OPENCODE_VAL"
echo "OBSIDIAN_VAULT_PATH=${OBSIDIAN_VAL:-<not found>}"
echo "TERMINAL=${TERMINAL_VAL:-<not found>}"
echo ""

# ── Prompt ──

read -rp "Write these to config.env? [Y/n] " answer
answer="${answer:-Y}"

if [[ "$answer" =~ ^[Nn] ]]; then
    # Let user edit
    EDITOR="${EDITOR:-}"
    if [ -z "$EDITOR" ]; then
        for ed in zed nano vim vi; do
            if command -v "$ed" &>/dev/null; then
                EDITOR="$ed"
                break
            fi
        done
    fi

    if [ -z "$EDITOR" ]; then
        echo "No editor found. Printing config.env to stdout instead."
        cat <<EOF
# ── Identity ──
USERNAME=$USERNAME_VAL
USER_UID=$UID_VAL
USER_GID=$GID_VAL

# ── Paths ──
OPENCODE_PATH=$OPENCODE_VAL
OBSIDIAN_VAULT_PATH=$OBSIDIAN_VAL

# ── Default app ──
TERMINAL=$TERMINAL_VAL
EOF
        exit 0
    fi

    TMPFILE=$(mktemp)
    cat > "$TMPFILE" <<EOF
# ── Identity ──
USERNAME=$USERNAME_VAL
USER_UID=$UID_VAL
USER_GID=$GID_VAL

# ── Paths ──
OPENCODE_PATH=$OPENCODE_VAL
OBSIDIAN_VAULT_PATH=$OBSIDIAN_VAL

# ── Default app ──
TERMINAL=$TERMINAL_VAL
EOF

    "$EDITOR" "$TMPFILE"
    cp "$TMPFILE" "$CONFIG_ENV"
    rm "$TMPFILE"
else
    cat > "$CONFIG_ENV" <<EOF
# ── Identity ──
USERNAME=$USERNAME_VAL
USER_UID=$UID_VAL
USER_GID=$GID_VAL

# ── Paths ──
OPENCODE_PATH=$OPENCODE_VAL
OBSIDIAN_VAULT_PATH=$OBSIDIAN_VAL

# ── Default app ──
TERMINAL=$TERMINAL_VAL
EOF
fi

echo "config.env written to $CONFIG_ENV"
