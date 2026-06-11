#!/bin/bash
set -euo pipefail

MODE="${1:-unrestricted}"

ALLOWLIST_DIR="/opt/allowlist"
ALLOWLIST_FILE="$ALLOWLIST_DIR/allowlist.txt"
ENV_FILE="$ALLOWLIST_DIR/env"
TMP_DIR="/tmp/generate-policies"

BRAVE_TEMPLATE="$ALLOWLIST_DIR/brave-policy.json.template"
FIREFOX_TEMPLATE="$ALLOWLIST_DIR/firefox-policies.json.template"

BRAVE_DEST="/etc/brave/policies/managed/policy.json"
FIREFOX_DEST="/etc/firefox/policies/policies.json"
CHROMIUM_DEST="/etc/chromium/policies/managed/policy.json"
CHROME_DEST="/etc/opt/chrome/policies/managed/policy.json"

# ---------------------------------------------------------------------------
# 1. Source NextDNS config ID (if available)
# ---------------------------------------------------------------------------
NEXTDNS_ID=""
if [ -f "$ENV_FILE" ]; then
    source "$ENV_FILE"
    NEXTDNS_ID="${NEXTDNS_CONFIG_ID:-}"
fi

if [ -z "$NEXTDNS_ID" ]; then
    echo "Warning: NEXTDNS_CONFIG_ID not set in $ENV_FILE"
    echo "         DNS-over-HTTPS will use placeholder"
    NEXTDNS_ID="YOUR_NEXTDNS_ID"
fi

# ---------------------------------------------------------------------------
# 2. Determine blocklist/allowlist based on mode
# ---------------------------------------------------------------------------
if [ "$MODE" = "unrestricted" ]; then
    CHROME_BLOCKLIST="[]"
    CHROME_ALLOWLIST="[]"
    FIREFOX_BLOCKLIST="[]"
    FIREFOX_ALLOWLIST="[]"
    echo "Mode: unrestricted (no URL filtering)"
else
    if [ ! -f "$ALLOWLIST_FILE" ] || [ ! -s "$ALLOWLIST_FILE" ]; then
        echo "Error: $ALLOWLIST_FILE is missing or empty — cannot enable locked mode"
        exit 1
    fi

    CHROME_ENTRIES=()
    FIREFOX_ENTRIES=()

    while IFS= read -r domain || [ -n "$domain" ]; do
        domain="$(echo "$domain" | xargs)"
        [ -z "$domain" ] && continue
        chrome_domain="$domain"
        if [[ "$chrome_domain" == \*\.* ]]; then
            chrome_domain="[*.]${chrome_domain#\*.}"
        fi
        CHROME_ENTRIES+=("\"$chrome_domain\"")
        FIREFOX_ENTRIES+=("\"*://${domain}/*\"")
    done < "$ALLOWLIST_FILE"

    build_json_array() {
        local entries=("$@")
        local first=true
        local result="["
        for entry in "${entries[@]}"; do
            if [ "$first" = true ]; then
                first=false
            else
                result+=", "
            fi
            result+="$entry"
        done
        result+="]"
        echo "$result"
    }

    CHROME_BLOCKLIST='["*", "my.nextdns.io"]'
    CHROME_ALLOWLIST="$(build_json_array "${CHROME_ENTRIES[@]}")"
    FIREFOX_BLOCKLIST='["<all_urls>"]'
    FIREFOX_ALLOWLIST="$(build_json_array "${FIREFOX_ENTRIES[@]}")"

    echo "Mode: locked (URL filtering active — $(wc -l < "$ALLOWLIST_FILE") domains allowed)"
fi

# ---------------------------------------------------------------------------
# 3. Generate Brave/Chromium policy
# ---------------------------------------------------------------------------
mkdir -p "$TMP_DIR"
BRAVE_TMP=$(mktemp "$TMP_DIR/brave-policy.XXXXXX")
if [ -f "$BRAVE_TEMPLATE" ]; then
    sed -e "s|{{NEXTDNS_ID}}|$NEXTDNS_ID|g" \
        -e "s|{{BLOCKLIST}}|$CHROME_BLOCKLIST|g" \
        -e "s|{{ALLOWLIST}}|$CHROME_ALLOWLIST|g" \
        "$BRAVE_TEMPLATE" > "$BRAVE_TMP"
else
    echo "Error: Brave template not found at $BRAVE_TEMPLATE"
    exit 1
fi

# ---------------------------------------------------------------------------
# 4. Generate Firefox policy
# ---------------------------------------------------------------------------
FIREFOX_TMP=$(mktemp "$TMP_DIR/firefox-policies.XXXXXX")
if [ -f "$FIREFOX_TEMPLATE" ]; then
    sed -e "s|{{NEXTDNS_ID}}|$NEXTDNS_ID|g" \
        -e "s|{{BLOCKLIST}}|$FIREFOX_BLOCKLIST|g" \
        -e "s|{{ALLOWLIST}}|$FIREFOX_ALLOWLIST|g" \
        "$FIREFOX_TEMPLATE" > "$FIREFOX_TMP"
else
    echo "Error: Firefox template not found at $FIREFOX_TEMPLATE"
    exit 1
fi

# ---------------------------------------------------------------------------
# 5. Deploy to system directories
# ---------------------------------------------------------------------------
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

# Remove stale policy files from previous versions that could conflict
for dir in /etc/brave /etc/chromium /etc/opt/chrome /etc/firefox; do
    sudo rm -f "$dir/policies/managed/kiosk_policy.json" 2>/dev/null || true
done

echo ""
echo "Deploying browser policies..."
deploy "$BRAVE_TMP" "$BRAVE_DEST"
deploy "$BRAVE_TMP" "$CHROMIUM_DEST"
deploy "$BRAVE_TMP" "$CHROME_DEST"
deploy "$FIREFOX_TMP" "$FIREFOX_DEST"

# Lock down policy directories
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

# Cleanup
rm -rf "$TMP_DIR"
