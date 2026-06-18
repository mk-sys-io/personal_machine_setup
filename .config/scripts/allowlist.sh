#!/bin/bash
set -euo pipefail

ALLOWLIST_DIR="/opt/allowlist"
INFRA_FILE="$ALLOWLIST_DIR/allowlist.infra.txt"
BASE_FILE="$ALLOWLIST_DIR/allowlist.base.txt"
SESSION_FILE="$ALLOWLIST_DIR/allowlist.session.txt"
GENERATE_DNSMASQ="$ALLOWLIST_DIR/generate-dnsmasq.sh"
GENERATE_POLICIES="$ALLOWLIST_DIR/generate-policies.sh"
GENERATE_NFTABLES="$ALLOWLIST_DIR/generate-nftables.sh"
VERIFY_SCRIPT="$ALLOWLIST_DIR/verify.sh"
MODE_FILE="$ALLOWLIST_DIR/mode"

usage() {
    echo "Usage: allowlist <command> [args]"
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
    echo "  seal               Generate random root password, seal with timelock"
    echo ""
    echo "Editing: sudo <editor> /opt/allowlist/allowlist.<section>.txt"
    echo "  Sections: infra (backend, no bookmarks)"
    echo "            base  (permanent browsing, bookmarked)"
    echo "            session (temporary browsing, bookmarked, clearable)"
    echo ""
    echo "  Run 'allowlist unlock' before editing, 'allowlist lock' after."
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
    if sudo "$GENERATE_DNSMASQ" "$mode" && sudo "$GENERATE_POLICIES" "$mode" && sudo "$GENERATE_NFTABLES" "$mode"; then
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
        echo "  sudo <editor> /opt/allowlist/allowlist.base.txt"
        exit 1
    fi
    if regenerate locked; then
        echo "Locked — only allowlisted domains reachable via dnsmasq"
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

seal() {
    REAL_HOME=$(getent passwd mike | cut -d: -f6)
    SEAL_DIR="$REAL_HOME/.config/seal"

    TLE_BIN=""
    for candidate in "/usr/local/bin/tle" "$REAL_HOME/go/bin/tle"; do
        if [ -x "$candidate" ]; then
            TLE_BIN="$candidate"
            break
        fi
    done

    if [ -z "$TLE_BIN" ]; then
        echo "Error: tle not found at /usr/local/bin/tle or $REAL_HOME/go/bin/tle" >&2
        exit 1
    fi

    if [ "$(current_mode)" = "locked" ]; then
        echo "ERROR: System is locked. Run allowlist unlock first, then re-run seal."
        echo "       tle needs unrestricted DNS to reach the drand timelock network."
        exit 1
    fi

    CRED_FILE="$SEAL_DIR/recovery-credentials"
    if [ ! -f "$CRED_FILE" ]; then
        echo "Error: $CRED_FILE not found"
        echo "       Create it with:"
        echo "         echo 'root_password=<your-root-password>' > $CRED_FILE"
        echo "         chmod 600 $CRED_FILE"
        exit 1
    fi

    echo "Select timelock duration:"
    echo "  1) 30 minutes"
    echo "  2) 1 hour"
    echo "  3) 3 hours"
    echo "  4) 1 day"
    echo "  5) 3 days"
    echo "  6) 7 days"
    echo "  7) Custom (e.g. 30m, 4h, 7d)"
    read -r choice || true
    case "$choice" in
        1) DURATION="30m" ;;
        2) DURATION="1h" ;;
        3) DURATION="3h" ;;
        4) DURATION="1d" ;;
        5) DURATION="3d" ;;
        6) DURATION="7d" ;;
        7) read -r -p "Enter duration (e.g. 30m, 4h, 7d): " DURATION || true
           if ! echo "$DURATION" | grep -qP '^\d+[mhd]$'; then
               echo "Error: invalid format. Use e.g. 30m, 4h, 7d" >&2
               exit 1
           fi
           ;;
        "") echo "Cancelled."; exit 1 ;;
        *) echo "Invalid choice"; exit 1 ;;
    esac

    EXPIRY=$(date -u -d "+$(echo "$DURATION" | sed 's/m/ minutes/; s/h/ hours/; s/d/ days/')" "+%Y-%m-%d %H:%M:%S UTC")

    echo ""
    echo "============================================="
    echo "  You are about to seal the system."
    echo "============================================="
    echo ""
    echo "  Timelock:     $DURATION"
    echo "  Credentials:  $CRED_FILE"
    echo ""
    echo "  This will:"
    echo "    - Check connectivity to drand timelock network"
    echo "    - Generate a random root password and change it"
    echo "    - Append the new password to recovery-credentials"
    echo "    - Encrypt the credentials with timelock"
    echo "    - Permanently shred the plaintext copy"
    echo "    - Lock the allowlist + firewall"
    echo "    - Wipe bash history (mike)"
    echo "    - Clear clipboard history (copyq + wl-copy)"
    echo ""
    read -r -p "Proceed? [y/N] " confirm || true
    case "$confirm" in
        y|Y|yes|YES) ;;
        *) echo "Cancelled."; exit 1 ;;
    esac

    mkdir -p "$SEAL_DIR"
    > "$SEAL_DIR/seal.log"
    echo "[$(date -u '+%Y-%m-%d %H:%M:%S UTC')] seal: started (duration: $DURATION, expires: $EXPIRY)" >> "$SEAL_DIR/seal.log"

    echo ""
    echo "Checking connectivity to drand timelock network..."
    echo "[$(date -u '+%Y-%m-%d %H:%M:%S UTC')] seal: checking drand network..." >> "$SEAL_DIR/seal.log"
    DRAND_REACHABLE=false
    for url in "https://api.drand.sh/" "https://api2.drand.sh/" \
               "https://api3.drand.sh/" "https://drand.cloudflare.com/"; do
        if curl -s --max-time 5 "$url" >/dev/null 2>&1; then
            DRAND_REACHABLE=true
            break
        fi
    done
    if ! $DRAND_REACHABLE; then
        echo "Error: Cannot reach drand timelock network." >&2
        echo "       tle encryption requires HTTPS access to drand beacon servers." >&2
        echo "       Check your internet connection and try again." >&2
        echo "[$(date -u '+%Y-%m-%d %H:%M:%S UTC')] seal: NETWORK CHECK FAILED" >> "$SEAL_DIR/seal.log"
        exit 1
    fi
    echo "drand network reachable."
    echo "[$(date -u '+%Y-%m-%d %H:%M:%S UTC')] seal: network check OK" >> "$SEAL_DIR/seal.log"
    echo ""

    echo "Generating random root password..."
    echo "[$(date -u '+%Y-%m-%d %H:%M:%S UTC')] seal: changing root password..." >> "$SEAL_DIR/seal.log"
    ROOT_PASSWORD=$(openssl rand -base64 48)
    if [ -z "$ROOT_PASSWORD" ]; then
        echo "Error: Failed to generate random password (openssl failed)" >&2
        echo "[$(date -u '+%Y-%m-%d %H:%M:%S UTC')] seal: ROOT PASSWORD GENERATION FAILED" >> "$SEAL_DIR/seal.log"
        exit 1
    fi
    if ! echo "root:$ROOT_PASSWORD" | chpasswd; then
        echo "Error: Failed to change root password" >&2
        echo "[$(date -u '+%Y-%m-%d %H:%M:%S UTC')] seal: ROOT PASSWORD CHANGE FAILED" >> "$SEAL_DIR/seal.log"
        exit 1
    fi
    sed -i '/^root_password=/d' "$CRED_FILE"
    echo "root_password=$ROOT_PASSWORD" >> "$CRED_FILE"
    chmod 600 "$CRED_FILE" 2>/dev/null || true
    echo "New root password: $ROOT_PASSWORD"
    echo "  (write this down if testing with timeshift rollback)"
    unset ROOT_PASSWORD
    echo "Root password changed, stale entries stripped, saved to $CRED_FILE"
    echo "[$(date -u '+%Y-%m-%d %H:%M:%S UTC')] seal: root password changed, stale entries stripped, saved to recovery-credentials" >> "$SEAL_DIR/seal.log"

    rm -f "$SEAL_DIR/sealed-credentials"

    TMPFILE=$(mktemp /tmp/seal.XXXXXX)
    cp "$CRED_FILE" "$TMPFILE"

    if ! grep -q '^root_password=' "$TMPFILE"; then
        echo "Error: recovery-credentials missing root_password entry" >&2
        echo "[$(date -u '+%Y-%m-%d %H:%M:%S UTC')] seal: MISSING ROOT PASSWORD IN FILE" >> "$SEAL_DIR/seal.log"
        rm -f "$TMPFILE"
        exit 1
    fi

    echo "[$(date -u '+%Y-%m-%d %H:%M:%S UTC')] seal: encrypting with tle..." >> "$SEAL_DIR/seal.log"

    if ! "$TLE_BIN" -e -D "$DURATION" --armor -o "$SEAL_DIR/sealed-credentials" "$TMPFILE" 2>> "$SEAL_DIR/seal.log"; then
        echo "[$(date -u '+%Y-%m-%d %H:%M:%S UTC')] seal: ENCRYPTION FAILED" >> "$SEAL_DIR/seal.log"
        echo "Error: tle encryption failed" >&2
        shred -u "$TMPFILE" 2>/dev/null || rm -f "$TMPFILE"
        exit 1
    fi
    echo "[$(date -u '+%Y-%m-%d %H:%M:%S UTC')] seal: encryption OK" >> "$SEAL_DIR/seal.log"
    shred -u "$TMPFILE" 2>/dev/null || rm -f "$TMPFILE"

    chown mike:mike "$SEAL_DIR/sealed-credentials" 2>/dev/null || true
    chmod 644 "$SEAL_DIR/sealed-credentials"
    chown -R mike:mike "$SEAL_DIR"

    shred -u "$CRED_FILE" 2>/dev/null || rm -f "$CRED_FILE"
    echo ""
    echo "Credentials encrypted with timelock ($DURATION):"
    echo "  Sealed:    $SEAL_DIR/sealed-credentials"
    echo "  Expires:   $EXPIRY"

    echo "Clearing clipboard history..."
    MIKE_UID=$(id -u mike)
    XDG_RUNTIME_DIR_ENV="/run/user/$MIKE_UID"
    sudo -u mike XDG_RUNTIME_DIR="$XDG_RUNTIME_DIR_ENV" WAYLAND_DISPLAY=wayland-1 wl-copy --clear 2>/dev/null || true
    sudo -u mike XDG_RUNTIME_DIR="$XDG_RUNTIME_DIR_ENV" DBUS_SESSION_BUS_ADDRESS="unix:path=$XDG_RUNTIME_DIR_ENV/bus" copyq clear 2>/dev/null || true
    pkill -u mike copyq 2>/dev/null || true
    rm -f "$REAL_HOME/.config/copyq/copyq_tab_"*.dat 2>/dev/null || true
    rm -f "$REAL_HOME/.config/copyq/copyq_tabs.ini" 2>/dev/null || true
    rm -f "$REAL_HOME/.config/copyq/copyq.lock" 2>/dev/null || true
    rm -rf "$REAL_HOME/.local/share/copyq" 2>/dev/null || true

    # Lock at the very end — right before reboot
    echo "Locking system..."
    local total=0
    for f in "$ALLOWLIST_DIR"/allowlist.*.txt; do
        [ -f "$f" ] && total=$((total + $(count_domains "$f")))
    done
    if [ "$total" -eq 0 ]; then
        echo "ERROR: All allowlist files are empty — cannot lock"
        echo "       Add domains to an allowlist file and re-run seal"
        echo "       sealed-credentials has been written to $SEAL_DIR/sealed-credentials"
        exit 1
    fi
    regenerate locked || { echo "ERROR: Lock failed"; exit 1; }

    echo ""
    echo "============================================"
    echo "  SYSTEM IS NOW LOCKED — Rebooting..."
    echo "============================================"
    echo ""
    echo "To unlock after reboot, wait for the timelock to expire, then run:"
    echo ""
    echo "  unseal"
    echo ""
    echo "Rebooting in 10 seconds..."
    sleep 10

    # Truncate shell history files right before force-reboot
    > "$REAL_HOME/.bash_history" 2>/dev/null || true
    > "$REAL_HOME/.zsh_history" 2>/dev/null || true
    > "$REAL_HOME/.zhistory" 2>/dev/null || true
    reboot -f
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
        seal
        ;;
    *)
        usage
        ;;
esac
