#!/bin/bash
set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

MODE_FILE="@ALLOWLIST_PATH@/mode"
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
echo "  Internet Lockdown — Verification"
echo "========================================="
echo ""

CURRENT_MODE="unrestricted"
if [ -f "$MODE_FILE" ]; then
    CURRENT_MODE=$(cat "$MODE_FILE")
fi
echo "  Current mode: $CURRENT_MODE"
echo ""

# ---------------------------------------------------------------------------
# 1. Mode file
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
NFT_DNS_RULES=$(sudo nft list ruleset 2>/dev/null | grep -c "skuid @USER_UID@.*dport { 53, 853 }" || true)
if [ "$CURRENT_MODE" = "locked" ]; then
    if [ "$NFT_DNS_RULES" -ge 2 ]; then
        pass "nftables: DNS drop rules present (locked mode)"
    else
        fail "nftables: expected at least 2 DNS drop rules, found $NFT_DNS_RULES"
    fi
else
    if [ "$NFT_DNS_RULES" -eq 0 ]; then
        pass "nftables: no DNS drop rules (unrestricted mode)"
    else
        fail "nftables: expected 0 DNS drop rules, found $NFT_DNS_RULES"
    fi
fi

# ---------------------------------------------------------------------------
# 3. dnsmasq daemon
# ---------------------------------------------------------------------------
echo "[3/9] dnsmasq daemon"
if systemctl is-active dnsmasq &>/dev/null; then
    pass "dnsmasq is running"
else
    fail "dnsmasq is not running"
fi

# ---------------------------------------------------------------------------
# 4. System DNS config
# ---------------------------------------------------------------------------
echo "[4/9] System DNS resolver"
if grep -q "nameserver 127.0.0.1" /etc/resolv.conf 2>/dev/null; then
    pass "system DNS points to 127.0.0.1 (dnsmasq)"
else
    RESOLV=$(cat /etc/resolv.conf 2>/dev/null || echo "missing")
    echo -e "  ${YELLOW}INFO${NC}  system DNS: $(echo "$RESOLV" | grep nameserver | head -1)"
fi

if lsattr /etc/resolv.conf 2>/dev/null | grep -q "^....i"; then
    pass "resolv.conf is immutable (chattr +i)"
else
    echo -e "  ${YELLOW}INFO${NC}  resolv.conf is not immutable"
fi

# ---------------------------------------------------------------------------
# 5. DNS leak test (user — affected by nftables)
# ---------------------------------------------------------------------------
echo "[5/9] DNS leak (user @USERNAME@)"
if [ "$EUID" -eq 0 ]; then
    DNS_TEST=$(su - @USERNAME@ -c 'python3 -c "
import socket
socket.setdefaulttimeout(3)
try:
    socket.getaddrinfo(\"snapchat.com\", 80)
    print(\"reachable\")
except Exception:
    print(\"blocked\")
"' 2>&1)
else
    DNS_TEST=$(python3 -c "
import socket
socket.setdefaulttimeout(3)
try:
    socket.getaddrinfo('snapchat.com', 80)
    print('reachable')
except Exception:
    print('blocked')
" 2>&1)
fi

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
# 6. DNS via root (UID 0 — always works)
# ---------------------------------------------------------------------------
echo "[6/9] DNS via root (UID 0 bypasses nftables)"
if [ "$EUID" -eq 0 ]; then
    if timeout 5 getent hosts github.com &>/dev/null; then
        pass "root DNS resolution works (UID 0 bypass)"
    else
        fail "root DNS resolution failed (expected reachable)"
    fi
elif sudo -n true 2>/dev/null; then
    if timeout 5 sudo getent hosts github.com &>/dev/null; then
        pass "sudo DNS resolution works (UID 0 bypass)"
    else
        fail "sudo DNS resolution failed (expected reachable)"
    fi
else
    skip "sudo DNS test skipped (run with cached sudo for this check)"
fi

# ---------------------------------------------------------------------------
# 7. Container internet (podman)
# ---------------------------------------------------------------------------
if ! command -v podman &>/dev/null; then
    echo "[7/10] Container internet (podman)"
    skip "podman not installed, skipping container test"
else
    echo "[7a/10] Container internet: apk update"
    if timeout 60 podman run --rm alpine apk update &>/dev/null; then
        pass "container: apk update (alpine repos reachable)"
    else
        fail "container: apk update failed"
    fi

    echo "[7b/10] Container internet: pip install six"
    if timeout 120 podman run --rm python:3-alpine pip install six -q &>/dev/null; then
        pass "container: pip install six (PyPI reachable)"
    else
        fail "container: pip install six failed"
    fi

    echo "[7c/10] Container internet: npm install left-pad"
    if timeout 120 podman run --rm node:alpine sh -c "cd /tmp && npm init -y >/dev/null 2>&1 && npm install left-pad --no-audit --no-fund" &>/dev/null; then
        pass "container: npm install left-pad (npm registry reachable)"
    else
        fail "container: npm install left-pad failed"
    fi
fi

# ---------------------------------------------------------------------------
# 8. tle binary (Phase 4 prerequisite)
# ---------------------------------------------------------------------------
echo "[8/10] tle binary (Phase 4 prerequisite)"
TLE_PATH=""
for candidate in "/usr/local/bin/tle" "$HOME/go/bin/tle"; do
    if [ -x "$candidate" ]; then
        TLE_PATH="$candidate"
        break
    fi
done
if [ -z "$TLE_PATH" ] && [ "$EUID" -eq 0 ]; then
    MIKE_HOME=$(getent passwd @USERNAME@ | cut -d: -f6)
    if [ -x "$MIKE_HOME/go/bin/tle" ]; then
        TLE_PATH="$MIKE_HOME/go/bin/tle"
    fi
fi
if [ -n "$TLE_PATH" ]; then
    pass "tle found at $TLE_PATH"
else
    fail "tle not found (/usr/local/bin/tle, \$HOME/go/bin/tle)"
fi

# ---------------------------------------------------------------------------
# 9. unseal binary
# ---------------------------------------------------------------------------
echo "[9/10] unseal binary"
if [ -x @LOCKDOWN_BIN_PATH@/unseal ]; then
    pass "unseal found at @LOCKDOWN_BIN_PATH@/unseal"
else
    fail "unseal not found at @LOCKDOWN_BIN_PATH@/unseal"
fi

# ---------------------------------------------------------------------------
# 10. PolicyKit lockdown + dnsmasq enabled
# ---------------------------------------------------------------------------
echo "[10/10] PolicyKit lockdown + dnsmasq enabled"
if sudo test -f /etc/polkit-1/rules.d/99-internet-lockdown.rules; then
    pass "PolicyKit rule deployed at /etc/polkit-1/rules.d/99-internet-lockdown.rules"
else
    fail "PolicyKit rule not found — re-run install.sh to deploy"
fi
if systemctl is-enabled dnsmasq &>/dev/null; then
    pass "dnsmasq service is enabled"
else
    fail "dnsmasq service is not enabled"
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
