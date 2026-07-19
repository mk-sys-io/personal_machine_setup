#!/bin/bash
set -euo pipefail

MODE="${1:-unrestricted}"

BASE_TEMPLATE="@LOCKDOWN_DATA_PATH@/nftables.conf.base"
LOCKED_TEMPLATE="@LOCKDOWN_DATA_PATH@/nftables.conf.locked"
DEST="/etc/nftables.conf"

if [ "$MODE" = "locked" ]; then
    if [ ! -f "$LOCKED_TEMPLATE" ]; then
        echo "Error: locked template not found at $LOCKED_TEMPLATE"
        exit 1
    fi
    sudo cp "$LOCKED_TEMPLATE" "$DEST"
    sudo chown root:root "$DEST"
    sudo chmod 644 "$DEST"
    echo "nftables: locked (DNS-leak prevention active)"
else
    if [ ! -f "$BASE_TEMPLATE" ]; then
        echo "Error: base template not found at $BASE_TEMPLATE"
        exit 1
    fi
    sudo cp "$BASE_TEMPLATE" "$DEST"
    sudo chown root:root "$DEST"
    sudo chmod 644 "$DEST"
    echo "nftables: unrestricted (no kernel restrictions)"
fi

sudo systemctl restart nftables

# Post-deploy health check: verify dnsmasq is responsive
sleep 1
if ! timeout 3 bash -c 'echo > /dev/tcp/127.0.0.1/53' 2>/dev/null; then
    logger -t generate-nftables "dnsmasq not reachable on 127.0.0.1:53 — attempting restart"
    systemctl restart dnsmasq 2>/dev/null || true
    sleep 1
    if ! timeout 3 bash -c 'echo > /dev/tcp/127.0.0.1/53' 2>/dev/null; then
        logger -t generate-nftables "ERROR: dnsmasq still unresponsive after restart"
        echo "Warning: DNS resolver (dnsmasq) is not responding" >&2
        echo "  Check: systemctl status dnsmasq" >&2
    fi
fi

echo "nftables: deployed and restarted"
