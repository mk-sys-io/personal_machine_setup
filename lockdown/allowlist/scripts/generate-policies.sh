#!/bin/bash
set -euo pipefail

ALLOWLIST_DIR="@ALLOWLIST_PATH@"

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
        sudo find "$dir" -type d -exec chmod 755 {} \;
        sudo find "$dir" -type f -exec chmod 644 {} \;
    fi
done

# =========================================================================
# BOOKMARK GENERATION (from allowlist.base.txt + allowlist.session.txt)
# =========================================================================
generate_bookmarks() {
    local dest="$1"
    local tmp
    tmp=$(mktemp /tmp/bookmarks.XXXXXX)

    {
        echo '{ "ManagedBookmarks": ['
        local first=true
        for src in "$ALLOWLIST_DIR/allowlist.base.txt" "$ALLOWLIST_DIR/allowlist.session.txt"; do
            [ ! -f "$src" ] && continue
            while IFS= read -r line || [ -n "$line" ]; do
                line="$(echo "$line" | xargs)"
                [ -z "$line" ] && continue
                [[ "$line" == \#* ]] && continue
                line="${line%%#*}"
                line="$(echo "$line" | xargs)"
                [ -z "$line" ] && continue
                domain="${line#\*.}"
                name="$(echo "${domain%%.*}" | sed 's/^\(.\)/\U\1/')"
                $first || echo ','
                echo -n "    { \"name\": \"$name\", \"url\": \"https://$domain\" }"
                first=false
            done < "$src"
        done
        echo ''
        echo '  ]'
        echo '}'
    } > "$tmp"

    sudo mkdir -p "$(dirname "$dest")"
    sudo cp "$tmp" "$dest"
    sudo chown root:root "$dest"
    sudo chmod 644 "$dest"
    rm -f "$tmp"
    echo "  Deployed bookmarks: $dest"
}

echo ""
echo "Deploying bookmarks (from base + session)..."
for dir in brave chromium "opt/chrome"; do
    generate_bookmarks "/etc/$dir/policies/managed/bookmarks.json"
done

echo ""
echo "Done. Verify:"
echo "  Brave/Chrome: chrome://policy"
echo "  Firefox:      about:policies"
