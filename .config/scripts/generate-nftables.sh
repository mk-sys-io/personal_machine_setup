#!/bin/bash
set -euo pipefail

MODE="${1:-unrestricted}"

BASE_TEMPLATE="/opt/allowlist/nftables.conf.base"
LOCKED_TEMPLATE="/opt/allowlist/nftables.conf.locked"
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
echo "nftables: deployed and restarted"
