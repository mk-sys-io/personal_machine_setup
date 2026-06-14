#!/bin/bash
set -euo pipefail

ALLOWLIST_DIR="/opt/allowlist"
ALLOWLIST_FILE="$ALLOWLIST_DIR/allowlist.txt"
GENERATE_DNSMASQ="$ALLOWLIST_DIR/generate-dnsmasq.sh"
GENERATE_SCRIPT="$ALLOWLIST_DIR/generate-policies.sh"
GENERATE_NFTABLES="$ALLOWLIST_DIR/generate-nftables.sh"
VERIFY_SCRIPT="$ALLOWLIST_DIR/verify.sh"
MODE_FILE="$ALLOWLIST_DIR/mode"

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
    if [ ! -x "$GENERATE_DNSMASQ" ]; then
        echo "Warning: $GENERATE_DNSMASQ not found or not executable" >&2
        return 1
    fi
    if [ ! -x "$GENERATE_SCRIPT" ]; then
        echo "Warning: $GENERATE_SCRIPT not found or not executable" >&2
        return 1
    fi
    if [ ! -x "$GENERATE_NFTABLES" ]; then
        echo "Warning: $GENERATE_NFTABLES not found or not executable" >&2
        return 1
    fi
    if sudo "$GENERATE_DNSMASQ" "$mode" && sudo "$GENERATE_SCRIPT" "$mode" && sudo "$GENERATE_NFTABLES" "$mode"; then
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
    echo "$domain" | sudo tee -a "$ALLOWLIST_FILE" > /dev/null
    sudo sort -u -o "$ALLOWLIST_FILE" "$ALLOWLIST_FILE"
    echo "Added: $domain"
    redeploy_if_locked
}

remove() {
    local domain="$1"
    if ! grep -qxF "$domain" "$ALLOWLIST_FILE" 2>/dev/null; then
        echo "Not found in allowlist: $domain"
        exit 1
    fi
    sudo sh -c "grep -vxF '$domain' '$ALLOWLIST_FILE' > '${ALLOWLIST_FILE}.tmp' && mv '${ALLOWLIST_FILE}.tmp' '$ALLOWLIST_FILE'"
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
    if [ ! -s "$ALLOWLIST_FILE" ]; then
        echo "ERROR: Allowlist is empty. Add domains first:"
        echo "  sudo /opt/allowlist/allowlist.sh add <domain>"
        exit 1
    fi
    if regenerate locked; then
        echo "Locked — only allowlisted domains are reachable via dnsmasq"
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
    local count=0
    if [ -f "$ALLOWLIST_FILE" ]; then
        count=$(wc -l < "$ALLOWLIST_FILE")
    fi
    echo "Mode:       $mode"
    echo "Domains:    $count"
    echo "Allowlist:  /opt/allowlist/allowlist.txt"
}

seal() {
    REAL_HOME=$(getent passwd mike | cut -d: -f6)

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

    CRED_FILE="$REAL_HOME/.config/recovery-credentials"
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

    echo ""
    echo "============================================="
    echo "  You are about to seal the system."
    echo "============================================="
    echo ""
    echo "  Timelock:     $DURATION"
    echo "  Credentials:  $CRED_FILE"
    echo ""
    echo "  This will:"
    echo "    - Encrypt the credentials with timelock"
    echo "    - Permanently shred the plaintext copy"
    echo "    - Lock the allowlist + firewall"
    echo "    - Wipe bash history (mike + root)"
    echo ""
    read -r -p "Proceed? [y/N] " confirm || true
    case "$confirm" in
        y|Y|yes|YES) ;;
        *) echo "Cancelled."; exit 1 ;;
    esac

    rm -f "$REAL_HOME/.config/sealed-credentials" "$REAL_HOME/.config/sealed-credentials.meta"

    TMPFILE=$(mktemp /tmp/seal.XXXXXX)
    cp "$CRED_FILE" "$TMPFILE"

    mkdir -p "$REAL_HOME/.config"
    if ! "$TLE_BIN" -e -D "$DURATION" --armor -o "$REAL_HOME/.config/sealed-credentials" "$TMPFILE"; then
        echo "Error: tle encryption failed" >&2
        shred -u "$TMPFILE"
        exit 1
    fi
    shred -u "$TMPFILE"

    chown mike:mike "$REAL_HOME/.config/sealed-credentials" 2>/dev/null || true
    chmod 644 "$REAL_HOME/.config/sealed-credentials"

    META_FILE="$REAL_HOME/.config/sealed-credentials.meta"
    EXPIRY=$(date -u -d "+$(echo "$DURATION" | sed 's/m/ minutes/; s/h/ hours/; s/d/ days/')" "+%Y-%m-%d %H:%M:%S UTC")
    {
      echo "Created:  $(date -u +'%Y-%m-%d %H:%M:%S UTC')"
      echo "Duration: $DURATION"
      echo "Expires:  $EXPIRY"
    } > "$META_FILE"
    chown mike:mike "$META_FILE"
    chmod 644 "$META_FILE"

    shred -u "$CRED_FILE"
    echo ""
    echo "Credentials encrypted with timelock ($DURATION):"
    echo "  Sealed:    $REAL_HOME/.config/sealed-credentials"
    echo "  Expires:   $EXPIRY"
    echo "  Meta:      $META_FILE"

    > "$REAL_HOME/.bash_history" 2>/dev/null || true
    > /root/.bash_history 2>/dev/null || true

    # Lock at the very end — right before reboot
    echo "Locking system..."
    if [ ! -s "$ALLOWLIST_FILE" ]; then
        echo "ERROR: Allowlist is empty at $ALLOWLIST_FILE — cannot lock"
        echo "       Add domains to $ALLOWLIST_FILE and re-run seal"
        echo "       Recovery credentials have been saved to $REAL_HOME/.config/sealed-credentials"
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
    echo '  podman run --rm \'
    echo '    -v ~/.config:/host-config:rw \'
    echo '    --dns 1.1.1.1 \'
    echo '    alpine sh -c "'
    echo '      apk add -q curl tar'
    echo '      curl -fsSL -o /tmp/tlock.tar.gz \'
    echo '        https://github.com/drand/tlock/releases/download/v1.2.0/tlock_1.2.0_linux_amd64.tar.gz'
    echo '      tar xzf /tmp/tlock.tar.gz -C /usr/bin tle'
    echo '      /usr/bin/tle -d -o /host-config/recovery-credentials /host-config/sealed-credentials'
    echo '    "'
    echo ""
    echo "This writes root_password=... back to ~/.config/recovery-credentials."
    echo "Use 'su -' with the recovered root password to run allowlist commands."
    echo ""
    echo "Rebooting in 6 seconds..."
    sleep 6
    reboot
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
    seal)
        seal
        ;;
    *)
        usage
        ;;
esac
