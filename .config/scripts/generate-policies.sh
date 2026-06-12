#!/bin/bash
set -euo pipefail

ALLOWLIST_DIR="/opt/allowlist"

BRAVE_TEMPLATE="$ALLOWLIST_DIR/brave-policy.json.template"
FIREFOX_TEMPLATE="$ALLOWLIST_DIR/firefox-policies.json.template"

BRAVE_DEST="/etc/brave/policies/managed/policy.json"
FIREFOX_DEST="/etc/firefox/policies/policies.json"
CHROMIUM_DEST="/etc/chromium/policies/managed/policy.json"
CHROME_DEST="/etc/opt/chrome/policies/managed/policy.json"

deploy() {
    local src="$1"
    local dest="$2"
    local dir
    dir="$(dirname "$dest")"

    sudo mkdir -p "$dir"
    sudo cp "$src" "$dest"
    sudo chown root:root "$dest"
    sudo chmod 644 "$dest"
    echo "  Deployed: $dest"
}

for dir in /etc/brave /etc/chromium /etc/opt/chrome /etc/firefox; do
    sudo rm -f "$dir/policies/managed/kiosk_policy.json" 2>/dev/null || true
done

echo ""
echo "Deploying browser policies..."
deploy "$BRAVE_TEMPLATE" "$BRAVE_DEST"
deploy "$BRAVE_TEMPLATE" "$CHROMIUM_DEST"
deploy "$BRAVE_TEMPLATE" "$CHROME_DEST"
deploy "$FIREFOX_TEMPLATE" "$FIREFOX_DEST"

for dir in /etc/brave /etc/chromium /etc/opt/chrome /etc/firefox; do
    if [ -d "$dir" ]; then
        sudo chown -R root:root "$dir"
        sudo chmod -R 755 "$dir"
    fi
done

echo ""
echo "Done. Verify:"
echo "  Brave:    chrome://policy"
echo "  Firefox:  about:policies"
