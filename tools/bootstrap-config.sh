#!/usr/bin/env bash
set -euo pipefail

# bootstrap-config.sh — Auto-detect system values and generate config.env

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TEMPLATE="$REPO_ROOT/config.env.template"
CONFIG_ENV="$REPO_ROOT/config.env"

# ── Detection functions ──

detect_username() { whoami; }
detect_uid()      { id -u; }
detect_gid()      { id -g; }

detect_opencode_path() {
    echo "$HOME/.opencode"
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

detect_editor() {
    # Check system $EDITOR first
    if [ -n "${EDITOR:-}" ]; then
        if command -v "$EDITOR" &>/dev/null; then
            echo "$EDITOR"
            return
        fi
    fi
    
    # Fallback chain
    for ed in zed vim vi; do
        if command -v "$ed" &>/dev/null; then
            echo "$ed"
            return
        fi
    done
    
    # Last resort — warn in main()
    echo "nano"
}

# ── Fill template ──

fill_template() {
    local var_name var_value
    
    while IFS= read -r line; do
        # Pass comments and empty lines through
        if [[ "$line" =~ ^[[:space:]]*# ]] || [[ -z "$line" ]]; then
            echo "$line"
            continue
        fi
        
        # Extract variable name and value
        var_name="${line%%=*}"
        var_value="${line#*=}"
        
        # If value is empty, detect it
        if [[ -z "$var_value" ]]; then
            case "$var_name" in
                USERNAME)            var_value="$(detect_username)" ;;
                USER_UID)            var_value="$(detect_uid)" ;;
                USER_GID)            var_value="$(detect_gid)" ;;
                OPENCODE_PATH)       var_value="$(detect_opencode_path)" ;;
                OBSIDIAN_VAULT_PATH) var_value="$(detect_obsidian_vault)" ;;
                TERMINAL)            var_value="$(detect_terminal)" ;;
                DEFAULT_EDITOR)      var_value="$(detect_editor)" ;;
            esac
        fi
        
        echo "$var_name=$var_value"
    done < "$1"
}

# ── Main ──

if [[ ! -f "$TEMPLATE" ]]; then
    echo "ERROR: $TEMPLATE not found" >&2
    exit 1
fi

# Detect values for display
DETECTED_EDITOR="$(detect_editor)"
if [ "$DETECTED_EDITOR" = "nano" ] && ! command -v nano &>/dev/null; then
    echo "WARNING: No editor detected, fell back to nano" >&2
fi

echo "=== Detected values ==="
echo "USERNAME=$(detect_username)"
echo "USER_UID=$(detect_uid)"
echo "USER_GID=$(detect_gid)"
echo "OPENCODE_PATH=$(detect_opencode_path)"
echo "OBSIDIAN_VAULT_PATH=$(detect_obsidian_vault)"
echo "TERMINAL=$(detect_terminal)"
echo "DEFAULT_EDITOR=$DETECTED_EDITOR"
echo ""

read -rp "Write these to config.env? [Y/n] " answer
answer="${answer:-Y}"

if [[ "$answer" =~ ^[Nn] ]]; then
    # Fill template, then let user edit
    fill_template "$TEMPLATE" > "$CONFIG_ENV"
    "$DETECTED_EDITOR" "$CONFIG_ENV"
else
    fill_template "$TEMPLATE" > "$CONFIG_ENV"
fi

echo "config.env written to $CONFIG_ENV"
