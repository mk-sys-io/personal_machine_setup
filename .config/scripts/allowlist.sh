#!/bin/bash
set -euo pipefail

REPO_DIR="$HOME/linux_setup"
ALLOWLIST_FILE="$REPO_DIR/.config/allowlist.txt"
GENERATE_SCRIPT="$REPO_DIR/.config/scripts/generate-policies.sh"
GENERATE_NFTABLES="$REPO_DIR/.config/scripts/generate-nftables.sh"
VERIFY_SCRIPT="$REPO_DIR/.config/scripts/verify.sh"
MODE_FILE="$HOME/.config/allowlist-mode"

usage() {
    echo "Usage: allowlist <command> [args]"
    echo ""
    echo "Commands:"
    echo "  lock              Enable URL whitelist (only allowlist.txt domains)"
    echo "  unlock            Disable URL whitelist (all sites allowed)"
    echo "  toggle            Switch between locked and unrestricted"
    echo "  status            Show current mode and domain count"
    echo "  verify            Run full system verification"
    echo "  add    <domain>   Add a domain to the allowlist"
    echo "  remove <domain>   Remove a domain from the allowlist"
    echo "  search <pattern>  Search for domains matching pattern"
    echo "  list              List all allowed domains"
    exit 1
}

current_mode() {
    if [ -f "$MODE_FILE" ]; then
        cat "$MODE_FILE"
    else
        echo "unrestricted"
    fi
}

regenerate() {
    local mode="$1"
    if [ ! -x "$GENERATE_SCRIPT" ]; then
        echo "Warning: $GENERATE_SCRIPT not found or not executable" >&2
        return 1
    fi
    if [ ! -x "$GENERATE_NFTABLES" ]; then
        echo "Warning: $GENERATE_NFTABLES not found or not executable" >&2
        return 1
    fi
    if sudo "$GENERATE_SCRIPT" "$mode" && sudo "$GENERATE_NFTABLES" "$mode"; then
        echo "$mode" | sudo tee "$MODE_FILE" > /dev/null
        return 0
    fi
    return 1
}

redeploy_if_locked() {
    local mode
    mode="$(current_mode)"
    if [ "$mode" = "locked" ]; then
        regenerate locked || echo "Warning: redeploy failed" >&2
    fi
}

add() {
    local domain="$1"
    if grep -qxF "$domain" "$ALLOWLIST_FILE" 2>/dev/null; then
        echo "Already in allowlist: $domain"
        return
    fi
    echo "$domain" >> "$ALLOWLIST_FILE"
    sort -u -o "$ALLOWLIST_FILE" "$ALLOWLIST_FILE"
    echo "Added: $domain"
    redeploy_if_locked
}

remove() {
    local domain="$1"
    if ! grep -qxF "$domain" "$ALLOWLIST_FILE" 2>/dev/null; then
        echo "Not found in allowlist: $domain"
        exit 1
    fi
    grep -vxF "$domain" "$ALLOWLIST_FILE" > "${ALLOWLIST_FILE}.tmp"
    mv "${ALLOWLIST_FILE}.tmp" "$ALLOWLIST_FILE"
    echo "Removed: $domain"
    redeploy_if_locked
}

search() {
    local pattern="$1"
    grep -i "$pattern" "$ALLOWLIST_FILE" || echo "No matches for: $pattern"
}

list() {
    if [ ! -s "$ALLOWLIST_FILE" ]; then
        echo "(empty)"
    else
        cat "$ALLOWLIST_FILE"
    fi
}

lock() {
    if regenerate locked; then
        echo "Locked — only whitelisted domains are allowed"
    fi
}

unlock() {
    if regenerate unrestricted; then
        echo "Unlocked — all sites allowed (debloat + DoH still active)"
    fi
}

toggle() {
    local mode
    mode="$(current_mode)"
    if [ "$mode" = "locked" ]; then
        unlock
    else
        lock
    fi
}

status() {
    local mode
    mode="$(current_mode)"
    local count=0
    if [ -f "$ALLOWLIST_FILE" ]; then
        count=$(wc -l < "$ALLOWLIST_FILE")
    fi
    echo "Mode:       $mode"
    echo "Domains:    $count"
    echo "Allowlist:  $ALLOWLIST_FILE"
}

[ $# -lt 1 ] && usage

case "$1" in
    lock)
        lock
        ;;
    unlock)
        unlock
        ;;
    toggle)
        toggle
        ;;
    status)
        status
        ;;
    add)
        [ $# -lt 2 ] && usage
        add "$2"
        ;;
    remove)
        [ $# -lt 2 ] && usage
        remove "$2"
        ;;
    search)
        [ $# -lt 2 ] && usage
        search "$2"
        ;;
    list)
        list
        ;;
    verify)
        if [ -x "$VERIFY_SCRIPT" ]; then
            "$VERIFY_SCRIPT"
        else
            echo "Error: verify script not found at $VERIFY_SCRIPT" >&2
            exit 1
        fi
        ;;
    *)
        usage
        ;;
esac
