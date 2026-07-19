#!/bin/bash
set -euo pipefail

LOCKDOWN_DATA_DIR="@LOCKDOWN_DATA_PATH@"
INFRA_FILE="$LOCKDOWN_DATA_DIR/infra.txt"
BASE_FILE="$LOCKDOWN_DATA_DIR/base.txt"
SESSION_FILE="$LOCKDOWN_DATA_DIR/session.txt"
GENERATE_DNSMASQ="$LOCKDOWN_DATA_DIR/scripts/generate-dnsmasq.sh"
GENERATE_POLICIES="$LOCKDOWN_DATA_DIR/scripts/generate-policies.sh"
GENERATE_NFTABLES="$LOCKDOWN_DATA_DIR/scripts/generate-nftables.sh"
VERIFY_SCRIPT="$LOCKDOWN_DATA_DIR/scripts/verify.sh"
MODE_FILE="$LOCKDOWN_DATA_DIR/mode"

usage() {
    echo "Usage: lockdown <command> [args]"
    echo ""
    echo "Commands:"
    echo "  lock                Enable DNS whitelist (only allowlisted domains)"
    echo "  unlock              Disable DNS whitelist (all domains allowed)"
    echo "  toggle              Switch between locked and unrestricted"
    echo "  status              Show current mode and per-section counts"
    echo "  verify              Run full system verification"
    echo "  search  <pattern>   Search for domains matching pattern"
    echo "  list    [--section] List domains (--infra, --base, --session)"
    echo "  clear-session       Remove all session domains and redeploy"
    echo "  seal               Seal system credentials (root password, lockdown, reboot)"
    echo ""
    echo "Editing: sudo <editor> @LOCKDOWN_DATA_PATH@/allowlist.<section>.txt"
    echo "  Sections: infra (backend, no bookmarks)"
    echo "            base  (permanent browsing, bookmarked)"
    echo "            session (temporary browsing, bookmarked, clearable)"
    echo ""
    echo "  Run 'lockdown unlock' before editing, 'lockdown lock' after."
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
    if [ ! -x "$GENERATE_DNSMASQ" ]; then
        echo "Warning: $GENERATE_DNSMASQ not found or not executable" >&2
        return 1
    fi
    if [ ! -x "$GENERATE_POLICIES" ]; then
        echo "Warning: $GENERATE_POLICIES not found or not executable" >&2
        return 1
    fi
    if [ ! -x "$GENERATE_NFTABLES" ]; then
        echo "Warning: $GENERATE_NFTABLES not found or not executable" >&2
        return 1
    fi
    if sudo "$GENERATE_DNSMASQ" "$mode" && sudo "$GENERATE_POLICIES" && sudo "$GENERATE_NFTABLES" "$mode"; then
        echo "$mode" | sudo tee "$MODE_FILE" > /dev/null
        return 0
    fi
    return 1
}

section_file() {
    local section="$1"
    case "$section" in
        infra)   echo "$INFRA_FILE" ;;
        base)    echo "$BASE_FILE" ;;
        session) echo "$SESSION_FILE" ;;
        *)       echo "" ;;
    esac
}

search() {
    local pattern="$1"
    local found=0
    for f in "$INFRA_FILE" "$BASE_FILE" "$SESSION_FILE"; do
        [ ! -f "$f" ] && continue
        local name
        name=$(basename "$f" .txt | sed 's/allowlist.//')
        while IFS= read -r line; do
            echo "[$name] $line"
            found=1
        done < <(grep -i "$pattern" "$f" 2>/dev/null || true)
    done
    [ "$found" -eq 0 ] && echo "No matches for: $pattern"
}

list() {
    local section="$1"
    section="${section#--}"
    local show_section=false
    if [ -n "$section" ]; then
        show_section=true
    fi
    for pair in "infra:$INFRA_FILE" "base:$BASE_FILE" "session:$SESSION_FILE"; do
        local name="${pair%%:*}"
        local file="${pair#*:}"
        [ ! -f "$file" ] && continue
        if $show_section && [ "$name" != "$section" ]; then
            continue
        fi
        echo "=== $(echo "$name" | tr '[:lower:]' '[:upper:]') ==="
        cat "$file"
        echo ""
    done
}

clear_session() {
    sudo sh -c ': > "$SESSION_FILE"'
    echo "Cleared: session domains"
    local mode
    mode="$(current_mode)"
    if [ "$mode" = "locked" ]; then
        regenerate locked || echo "Warning: redeploy failed" >&2
        echo "  Note: Restart your browser to flush in-memory DNS caches"
    fi
}

count_domains() {
    local file="$1"
    if [ ! -f "$file" ]; then
        echo 0
        return
    fi
    grep -cvE '^\s*$|^#' "$file" 2>/dev/null || echo 0
}

lock() {
    local total=0
    for f in "$INFRA_FILE" "$BASE_FILE" "$SESSION_FILE"; do
        total=$((total + $(count_domains "$f")))
    done
    if [ "$total" -eq 0 ]; then
        echo "ERROR: All allowlist files are empty. Add domains first:"
        echo "  sudo <editor> @LOCKDOWN_DATA_PATH@/base.txt"
        exit 1
    fi
    if regenerate locked; then
        echo "Locked — only allowlisted domains reachable via dnsmasq"
        echo "  Note: Restart your browser to flush in-memory DNS caches"
    fi
}

unlock() {
    if regenerate unrestricted; then
        echo "Unlocked — all domains allowed"
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
    local infra base session
    infra=$(count_domains "$INFRA_FILE")
    base=$(count_domains "$BASE_FILE")
    session=$(count_domains "$SESSION_FILE")
    echo "Mode:       $mode"
    echo "Infra:      $infra  (backend, no bookmarks)"
    echo "Base:       $base   (permanent browsing, bookmarked)"
    echo "Session:    $session (temporary browsing, bookmarked)"
    echo "Total:      $((infra + base + session))"
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
    search)
        [ $# -lt 2 ] && usage
        search "$2"
        ;;
    list)
        list "${2:-}"
        ;;
    clear-session)
        clear_session
        ;;
    verify)
        if [ -x "$VERIFY_SCRIPT" ]; then
            "$VERIFY_SCRIPT"
        else
            echo "Error: verify script not found at $VERIFY_SCRIPT" >&2
            exit 1
        fi
        ;;
    seal)
        shift
        "$LOCKDOWN_DATA_DIR/scripts/seal.py" "$@"
        ;;
    *)
        usage
        ;;
esac
