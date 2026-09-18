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
# ---------------------------------------------------------------------------

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common.sh"

INTERFACES="/etc/network/interfaces"
REBOOT1_DOC="docs/bootstrap-wifi.md"

# ---------------------------------------------------------------------------
# 0. Preconditions (belt-and-braces for --skip 20 runs)
# ---------------------------------------------------------------------------

if ! cmd_exists nmcli; then
    log_warn "nmcli missing — NetworkManager not converged (stage-0/20 package gap)."
    exit 2
fi

if [[ ! -f "$INTERFACES" ]]; then
    log "No $INTERFACES — nothing to migrate."
    exit 2
fi

# ---------------------------------------------------------------------------
# 1. Detect wifi stanza, extract SSID/PSK
# ---------------------------------------------------------------------------

ssid_raw="$(grep -m1 -E '^[[:space:]]*wpa-ssid[[:space:]]+' "$INTERFACES" || true)"
psk_raw="$(grep -m1 -E '^[[:space:]]*wpa-psk[[:space:]]+' "$INTERFACES" || true)"

if [[ -z "$ssid_raw" ]]; then
    log "No wpa-ssid stanza in $INTERFACES — nothing to migrate."
    exit 2
fi

# Split on FIRST '='-or-whitespace boundary, strip matching quotes
# (mirrors load_env first-'=' rule; never truncate '='-bearing PSKs).
strip_val() {
    local v="$1"
    v="${v#"${v%%[![:space:]]*}"}"           # ltrim
    v="${v%\"}"; v="${v#\"}"                 # strip matching double quotes
    v="${v%\'}"; v="${v#\'}"                 # strip matching single quotes
    printf '%s' "$v"
}
ssid="$(strip_val "$(printf '%s' "$ssid_raw" | sed -E 's/^[[:space:]]*wpa-ssid[[:space:]]+//')")"
psk="$(strip_val "$(printf '%s' "$psk_raw" | sed -E 's/^[[:space:]]*wpa-psk[[:space:]]+//')")"

if [[ -z "$ssid" ]]; then
    log_error "Empty SSID in $INTERFACES — refusing to migrate. See $REBOOT1_DOC."
    exit 1
fi
if [[ -z "$psk" ]]; then
    log_error "SSID '$ssid' has no wpa-psk (open network?) — refusing to migrate. See $REBOOT1_DOC."
    exit 1
fi

wifi_iface="$(iw dev 2>/dev/null | awk '/Interface/ {print $2; exit}')"
if [[ -z "$wifi_iface" ]]; then
    log_error "No wireless interface found (iw dev empty). Check firmware: dmesg | grep -i firmware."
    exit 1
fi

# ---------------------------------------------------------------------------
# 2. Idempotency: profile exists and device connected → already converged
# ---------------------------------------------------------------------------

if nmcli -t -f NAME connection show 2>/dev/null | grep -qxF "$ssid"; then
    if nmcli -t -f DEVICE,TYPE,STATE device status 2>/dev/null \
        | grep -E "^${wifi_iface}:wifi:connected" >/dev/null; then
        log_ok "WiFi already migrated ('$ssid' connected on $wifi_iface)."
        exit 0
    fi
    log "Profile '$ssid' exists but not connected — activating."
    if sudo nmcli connection up "$ssid" 2>/dev/null; then
        log_ok "WiFi profile '$ssid' activated."
        exit 0
    fi
    log_warn "Activation failed — re-adding profile '$ssid'."
    sudo nmcli connection delete "$ssid" 2>/dev/null || true
fi

# ---------------------------------------------------------------------------
# 3. Enable NetworkManager (reuse enable_services pattern), then add profile
# ---------------------------------------------------------------------------

log_step "Migrating WiFi '$ssid' to NetworkManager"
if systemctl is-enabled NetworkManager &>/dev/null; then
    log_ok "NetworkManager already enabled"
else
    if sudo systemctl enable --now NetworkManager 2>/dev/null; then
        log_ok "NetworkManager enabled"
    else
        log_error "NetworkManager enable failed."
        exit 1
    fi
fi
sudo systemctl start NetworkManager 2>/dev/null || true

if ! sudo nmcli connection add type wifi con-name "$ssid" \
    ifname "$wifi_iface" ssid "$ssid" \
    wifi-sec.key-mgmt wpa-psk wifi-sec.psk "$psk" 2>/dev/null; then
    log_error "nmcli connection add failed for '$ssid'."
    exit 1
fi
log_ok "NM profile '$ssid' written"

# ---------------------------------------------------------------------------
# 4. Verify managed/connected — block reboot prompt on unmanaged
# ---------------------------------------------------------------------------

sleep 5
if nmcli -t -f DEVICE,TYPE,STATE device status 2>/dev/null \
    | grep -E "^${wifi_iface}:wifi:unmanaged" >/dev/null; then
    log_error "WIFI UNMANAGED: $wifi_iface is unmanaged — ifupdown still owns it. Reboot blocked."
    exit 1
fi
if ! nmcli -t -f DEVICE,TYPE,STATE device status 2>/dev/null \
    | grep -E "^${wifi_iface}:wifi:connected" >/dev/null; then
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
    $1 == "allow-hotplug" && $2 == iface { next }
    $1 == "auto" && $2 == iface { next }
    /^[[:space:]]*wpa-(ssid|psk)[[:space:]]/ { next }
    { print }
' "$INTERFACES" | sudo tee "$INTERFACES" > /dev/null
log_ok "WiFi stanza stripped from $INTERFACES (backup $backup)."

# ---------------------------------------------------------------------------
# 6. Final connectivity check — net must be alive before 40-restore
# ---------------------------------------------------------------------------

if ping -c1 -W10 9.9.9.9 &>/dev/null; then
    log_ok "Connectivity alive after migration."
    exit 0
else
    log_error "No connectivity after migration (backup $backup). Reboot blocked."
    exit 1
fi
