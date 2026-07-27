#!/bin/bash
set -euo pipefail

MODE="${1:-unrestricted}"

BASE_TEMPLATE="@ARK_DATA_PATH@/nftables.conf.base"
RESTRICTED_TEMPLATE="@ARK_DATA_PATH@/nftables.conf.restricted"
DEST="/etc/nftables.conf"

if [ "$MODE" = "unrestricted" ]; then
    if [ ! -f "$BASE_TEMPLATE" ]; then
        echo "Error: base template not found at $BASE_TEMPLATE"
        exit 1
    fi
    cp "$BASE_TEMPLATE" "$DEST"
    chown root:root "$DEST"
    chmod 644 "$DEST"
    echo "nftables: unrestricted (no kernel restrictions)"
else
    if [ ! -f "$RESTRICTED_TEMPLATE" ]; then
        echo "Error: restricted template not found at $RESTRICTED_TEMPLATE"
        exit 1
    fi
    cp "$RESTRICTED_TEMPLATE" "$DEST"
    chown root:root "$DEST"
    chmod 644 "$DEST"
    echo "nftables: $MODE (DNS-blocking rules active)"
fi

systemctl restart nftables

# Post-deploy health check: verify dnsmasq is responsive
sleep 1
if ! timeout 3 bash -c 'echo > /dev/tcp/127.0.0.1/53' 2>/dev/null; then
    logger -t generate-nftables "dnsmasq not reachable on 127.0.0.1:53 — attempting restart"
    systemctl restart dnsmasq 2>/dev/null || true
    sleep 1
    if ! timeout 3 bash -c 'echo > /dev/tcp/127.0.0.1/53' 2>/dev/null; then
        logger -t generate-nftables "ERROR: dnsmasq still unresponsive after restart"
        echo "Error: DNS resolver (dnsmasq) is not responding" >&2
        echo "  Check: systemctl status dnsmasq" >&2
        exit 1
    fi
fi

echo "nftables: deployed and restarted"
