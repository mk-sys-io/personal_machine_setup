#!/bin/bash
set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

MODE_FILE="$HOME/.config/allowlist-mode"
NEXTDNS_IP="192.168.1.1"
PASS=0
FAIL=0
SKIP=0

pass() { echo -e "  ${GREEN}PASS${NC}  $1"; PASS=$((PASS + 1)); }
fail() { echo -e "  ${RED}FAIL${NC}  $1"; FAIL=$((FAIL + 1)); }
skip() { echo -e "  ${YELLOW}SKIP${NC}  $1"; SKIP=$((SKIP + 1)); }

check() {
    local label="$1"
    local cmd="$2"
    local expected="$3"

    if output=$(eval "$cmd" 2>&1); then
        if [ "$expected" = "pass" ]; then
            pass "$label"
        else
            fail "$label (expected failure, but succeeded)"
        fi
    else
        local rc=$?
        if [ "$expected" = "fail" ]; then
            pass "$label (blocked as expected)"
        else
            fail "$label (exit $rc): $output"
        fi
    fi
}

echo "========================================="
echo "  White Internet Policy — Verification"
echo "========================================="
echo ""

CURRENT_MODE="unrestricted"
if [ -f "$MODE_FILE" ]; then
    CURRENT_MODE=$(cat "$MODE_FILE")
fi
echo "  Current mode: $CURRENT_MODE"
echo ""

# ---------------------------------------------------------------------------
# 1. Current mode file
# ---------------------------------------------------------------------------
echo "[1/9] Mode consistency"
if [ -f "$MODE_FILE" ]; then
    pass "mode file exists at $MODE_FILE"
else
    fail "mode file missing (expected at $MODE_FILE)"
fi

# ---------------------------------------------------------------------------
# 2. nftables rules match mode
# ---------------------------------------------------------------------------
echo "[2/9] nftables ruleset"
NFT_DNS_RULES=$(sudo nft list ruleset 2>/dev/null | grep -c "skuid 1000.*dport { 53, 853 }" || true)
if [ "$CURRENT_MODE" = "locked" ]; then
    if [ "$NFT_DNS_RULES" -ge 4 ]; then
        pass "nftables: DNS drop rules present (locked mode)"
    else
        fail "nftables: expected 4 DNS drop rules, found $NFT_DNS_RULES"
    fi
else
    if [ "$NFT_DNS_RULES" -eq 0 ]; then
        pass "nftables: no DNS drop rules (unrestricted mode)"
    else
        fail "nftables: expected 0 DNS drop rules, found $NFT_DNS_RULES"
    fi
fi

# ---------------------------------------------------------------------------
# 3. NextDNS daemon
# ---------------------------------------------------------------------------
echo "[3/9] NextDNS daemon"
if nextdns status 2>&1 | grep -q "running"; then
    pass "nextdns is running"
else
    fail "nextdns is not running"
fi

# ---------------------------------------------------------------------------
# 4. System DNS config
# ---------------------------------------------------------------------------
echo "[4/9] System DNS resolver"
RESOLV=$(cat /etc/resolv.conf 2>/dev/null || echo "missing")
if echo "$RESOLV" | grep -q "$NEXTDNS_IP"; then
    pass "system DNS points to $NEXTDNS_IP"
elif echo "$RESOLV" | grep -q "127.0.0.1"; then
    pass "system DNS points to localhost (NextDNS proxy)"
else
    echo -e "  ${YELLOW}INFO${NC}  system DNS: $(echo "$RESOLV" | grep nameserver | head -1)"
fi

# ---------------------------------------------------------------------------
# 5. DNS leak test (user — affected by nftables)
# ---------------------------------------------------------------------------
echo "[5/9] DNS leak (user mike)"
DNS_TEST=$(python3 -c "
import socket
socket.setdefaulttimeout(3)
try:
    socket.getaddrinfo('github.com', 80)
    print('reachable')
except Exception:
    print('blocked')
" 2>&1)

if [ "$CURRENT_MODE" = "locked" ]; then
    if echo "$DNS_TEST" | grep -qE "blocked|^error"; then
        pass "DNS resolution blocked as expected (locked mode)"
    else
        fail "DNS resolution succeeded (expected blocked)"
    fi
else
    if echo "$DNS_TEST" | grep -q "reachable"; then
        pass "DNS resolution works (unrestricted mode)"
    else
        fail "DNS resolution failed (expected reachable): $DNS_TEST"
    fi
fi

# ---------------------------------------------------------------------------
# 6. DNS via sudo (UID 0 — always works)
# ---------------------------------------------------------------------------
echo "[6/9] DNS via sudo (UID 0 bypasses nftables)"
if sudo -n true 2>/dev/null; then
    if timeout 5 sudo getent hosts github.com &>/dev/null; then
        pass "sudo DNS resolution works (UID 0 bypass)"
    else
        fail "sudo DNS resolution failed (expected reachable)"
    fi
else
    skip "sudo DNS test skipped (run with cached sudo for this check)"
fi

# ---------------------------------------------------------------------------
# 7. Container DNS (podman)
# ---------------------------------------------------------------------------
echo "[7/9] Container DNS (podman)"
if command -v podman &>/dev/null; then
    if [ "$CURRENT_MODE" = "locked" ]; then
        if timeout 15 podman run --rm alpine ping -c 1 -W 3 1.1.1.1 &>/dev/null; then
            skip "container DNS not blocked by host nftables (expected — podman uses its own netns)"
        else
            pass "container ping blocked in locked mode (expected)"
        fi
    else
        if timeout 15 podman run --rm alpine ping -c 1 -W 3 1.1.1.1 &>/dev/null; then
            pass "container ping works in unrestricted mode"
        else
            fail "container ping failed in unrestricted mode"
        fi
    fi
else
    skip "podman not installed, skipping container test"
fi

# ---------------------------------------------------------------------------
# 8. dee binary (Phase 4 prerequisite)
# ---------------------------------------------------------------------------
echo "[8/9] tle binary (Phase 4 prerequisite)"
TLE_PATH="$HOME/go/bin/tle"
if [ -x "$TLE_PATH" ]; then
    pass "tle found at $TLE_PATH"
else
    fail "tle not found at $TLE_PATH (run 'go install github.com/drand/tlock/cmd/tle@latest')"
fi

# ---------------------------------------------------------------------------
# 9. PolicyKit lockdown
# ---------------------------------------------------------------------------
echo "[9/9] PolicyKit lockdown"
if sudo test -f /etc/polkit-1/rules.d/99-internet-lockdown.rules; then
    pass "PolicyKit rule deployed at /etc/polkit-1/rules.d/99-internet-lockdown.rules"
else
    fail "PolicyKit rule not found — re-run install.sh to deploy"
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo ""
echo "========================================="
echo "  Results: $PASS pass, $FAIL fail, $SKIP skip"
echo "========================================="

if [ "$FAIL" -gt 0 ]; then
    exit 1
fi
exit 0
