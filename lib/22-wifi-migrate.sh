#!/usr/bin/env bash
set -euo pipefail

# ---------------------------------------------------------------------------
# 22-wifi-migrate.sh — Migrate Reboot-1 ifupdown WiFi to NetworkManager
#
# Called from install.py only (install.sh MODULES is frozen — do not add).
# Position: after 20-packages (NM installed), before 05-home-skeleton;
# net must be alive before 40-restore.
#
# Flow: detect ifupdown wifi stanza → extract SSID/PSK → enable NM →
# `nmcli con add` profile → verify managed/connected → strip wifi lines
# from interfaces (backup kept) → final connectivity check.
# Order note: NM is enabled BEFORE `nmcli con add` because nmcli needs
# the daemon to write the profile (deviates from plan's list order).
#
# Exit contract (0=pass, 1=fail, 2=skip, 3=partial):
#   0  migrated now, or already converged (idempotent re-run)
#   1  broken (unparsable stanza, unmanaged device, no connectivity) —
#      install.py MUST treat this as failure and block the reboot prompt
#   2  no wifi stanza ever existed — nothing to migrate, not a failure
# Signal to the future install.py runner on unmanaged: non-zero exit +
#   the greppable line: WIFI UNMANAGED (see below). Mechanism to be
#   defined in 20-install-py.md (Gap 2 handoff).
# Never silently deletes: backup kept, loopback-only strip only after
#   the NM profile verifies. interfaces.d/* out of scope (v1).
#
# Language decision (recorded 2026-09-18): KEEP bash per
#   20-install-py.md §20.3 — C5 dominates (thin syscall sequence; no
#   JSON/API/retry/merge content). install.py consumes this across a
#   process boundary (exit code + greppable WIFI UNMANAGED), so a Python
#   port gains no structured-error benefit.
#   Revisit (convert to Python) if any flip trigger fires:
#     (a) scope grows beyond v1 (interfaces.d/*, WPA3/SAE, multi-profile)
#     (b) partial-failure accounting (C4) needed beyond the 0/1/2 contract
#     (c) manual testing surfaces repeated implicit bugs rooted in the
#         bash language itself (quoting/splitting/set -e semantics) rather
#         than this script's logic — fixes treating symptoms, not causes —
#         forcing conversion for real error handling and test coverage.
# ---------------------------------------------------------------------------

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common.sh"

INTERFACES="/etc/network/interfaces"
REBOOT1_DOC="docs/bootstrap-wifi.md"

# Optional RESULT trailer (§20.4d): machine-readable outcome for the
# future install.py StepResult; harmless to human readers.
result() {
    local msg="${3//\\/\\\\}"
    msg="${msg//\"/\\\"}"
    printf 'RESULT {"status":"%s","changed":%s,"message":"%s"}\n' "$1" "$2" "$msg"
}

# ---------------------------------------------------------------------------
# 0. Preconditions (belt-and-braces for --skip 20 runs)
# ---------------------------------------------------------------------------

if ! cmd_exists nmcli; then
    log_warn "nmcli missing — NetworkManager not converged (stage-0/20 package gap)."
    result skip false "nmcli missing - package gap"
    exit 2
fi

if [[ ! -f "$INTERFACES" ]]; then
    log "No $INTERFACES — nothing to migrate."
    result skip false "no $INTERFACES"
    exit 2
fi

# ---------------------------------------------------------------------------
# 1. Detect wifi stanza, extract SSID/PSK
# ---------------------------------------------------------------------------

ssid_raw="$(grep -m1 -E '^[[:space:]]*wpa-ssid([[:space:]=]+|$)' "$INTERFACES" || true)"
psk_raw="$(grep -m1 -E '^[[:space:]]*wpa-psk([[:space:]=]+|$)' "$INTERFACES" || true)"

if [[ -z "$ssid_raw" ]]; then
    log "No wpa-ssid stanza in $INTERFACES — nothing to migrate."
    result skip false "no wpa-ssid stanza"
    exit 2
fi

# Split on FIRST separator run, strip one matched quote pair; unquoted
# values cut at whitespace/`#` (renderer comment semantics). `=`-bearing
# values pass through intact (mirrors load_env first-'=' rule).
strip_val() {
    local v="$1" q
    v="${v#"${v%%[![:space:]]*}"}"           # ltrim
    q="${v:0:1}"
    if [[ "$q" == '"' || "$q" == "'" ]]; then
        v="${v:1}"
        v="${v%%"$q"*}"                       # cut at matching close quote
    else
        v="${v%%[[:space:]#]*}"               # unquoted: cut at ws/comment
    fi
    printf '%s' "$v"
}
ssid="$(strip_val "$(printf '%s' "$ssid_raw" | sed -E 's/^[[:space:]]*wpa-ssid[[:space:]=]*//')")"
psk="$(strip_val "$(printf '%s' "$psk_raw" | sed -E 's/^[[:space:]]*wpa-psk[[:space:]=]*//')")"

if [[ -z "$ssid" ]]; then
    log_error "Empty SSID in $INTERFACES — refusing to migrate. See $REBOOT1_DOC."
    exit 1
fi
if [[ -z "$psk" ]]; then
    log_error "SSID '$ssid' has no wpa-psk (open network?) — refusing to migrate. See $REBOOT1_DOC."
    exit 1
fi

if ! cmd_exists iw; then
    log_error "iw missing — cannot identify the wireless interface (stage-0/20 package gap)."
    exit 2
fi
wifi_iface="$(iw dev | awk '/Interface/ {print $2; exit}')" || true
if [[ -z "$wifi_iface" ]]; then
    log_error "No wireless interface found (iw dev empty). Check firmware: dmesg | grep -i firmware."
    exit 1
fi

# Exact field match on `nmcli -t -f DEVICE,TYPE,STATE` output — no regex,
# so iface names pass through unescaped. stderr stays visible (evidence).
nm_state() {
    nmcli -t -f DEVICE,TYPE,STATE device status \
        | awk -F: -v iface="$wifi_iface" -v want="$1" '
            $1 == iface && $2 == "wifi" && $3 == want { found=1 }
            END { exit !found }'
}

# ---------------------------------------------------------------------------
# 2. Idempotency: profile exists and device connected → already converged
# ---------------------------------------------------------------------------

if nmcli -t -f NAME connection show | grep -qxF "$ssid"; then
    if nm_state connected; then
        log_ok "WiFi already migrated ('$ssid' connected on $wifi_iface)."
        result ok false "already migrated: $ssid"
        exit 0
    fi
    log "Profile '$ssid' exists but not connected — activating."
    if sudo nmcli connection up "$ssid"; then
        log_ok "WiFi profile '$ssid' activated."
        result ok true "activated $ssid"
        exit 0
    fi
    log_warn "Activation failed — re-adding profile '$ssid'."
    sudo nmcli connection delete "$ssid" || true
fi

# ---------------------------------------------------------------------------
# 3. Enable NetworkManager (reuse enable_services pattern), then add profile
# ---------------------------------------------------------------------------

log_step "Migrating WiFi '$ssid' to NetworkManager"
if systemctl is-enabled NetworkManager &>/dev/null; then
    log_ok "NetworkManager already enabled"
else
    if sudo systemctl enable --now NetworkManager; then
        log_ok "NetworkManager enabled"
    else
        log_error "NetworkManager enable failed."
        exit 1
    fi
fi
sudo systemctl start NetworkManager || true

if ! sudo nmcli connection add type wifi con-name "$ssid" \
    ifname "$wifi_iface" ssid "$ssid" \
    wifi-sec.key-mgmt wpa-psk wifi-sec.psk "$psk"; then
    log_error "nmcli connection add failed for '$ssid'."
    exit 1
fi
log_ok "NM profile '$ssid' written"

# ---------------------------------------------------------------------------
# 4. Verify managed/connected — block reboot prompt on unmanaged
# ---------------------------------------------------------------------------

# Poll for managed+connected: association/DHCP timing varies, so a fixed
# sleep flakes false-negative. Quiet loop (no WARN spam on the happy path —
# retry() warns per attempt, wrong signal for normal-but-slow DHCP).
log "Waiting for '$ssid' to associate (up to 30s)..."
deadline=$(( SECONDS + 30 ))
while ! nm_state connected; do
    if (( SECONDS >= deadline )); then
        break
    fi
    sleep 2
done
if nm_state unmanaged; then
    log_error "WIFI UNMANAGED: $wifi_iface is unmanaged — ifupdown still owns it. Reboot blocked."
    exit 1
fi
if ! nm_state connected; then
    log_error "WIFI UNMANAGED: $wifi_iface not connected after migration. Reboot blocked."
    exit 1
fi
log_ok "WiFi managed and connected ($wifi_iface)."

# ---------------------------------------------------------------------------
# 5. Strip wifi lines from interfaces (backup kept), keep the rest
# ---------------------------------------------------------------------------

backup="$INTERFACES.bak-$(date +%F-%H%M%S)"
sudo cp -a "$INTERFACES" "$backup"
log "Backup: $backup"

sudo awk -v iface="$wifi_iface" '
    $1 == "iface" && $2 == iface { skip=1; next }
    skip && /^[[:space:]]/ { next }
    { skip=0 }
    ($1 == "allow-hotplug" || $1 == "auto") {
        rest = ""
        for (i = 2; i <= NF; i++) if ($i != iface) rest = rest " " $i
        if (rest != "") print $1 rest
        next
    }
    /^[[:space:]]*wpa-(ssid|psk)([[:space:]=]|$)/ { next }
    { print }
' "$INTERFACES" | sudo tee "$INTERFACES" > /dev/null
log_ok "WiFi stanza stripped from $INTERFACES (backup $backup)."

# ---------------------------------------------------------------------------
# 6. Final connectivity check — net must be alive before 40-restore
# ---------------------------------------------------------------------------

if ping -c1 -W10 9.9.9.9 &>/dev/null; then
    log_ok "Connectivity alive after migration."
    result ok true "migrated $ssid to NetworkManager"
    exit 0
else
    log_error "No connectivity after migration (backup $backup). Reboot blocked."
    exit 1
fi
