#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# capture-network-state.sh — one-shot post-installer network evidence dump.
#
# TEMPORARY: run exactly once by tools/bootstrap.sh from the Ventoy stick,
# immediately after the stick is mounted and before stage-0 apt. It takes no
# arguments (argv -> exit 2), installs nothing, prompts for no input beyond
# sudo, and always exits 0 so the bootstrap can continue.
#
# Writes:
#   ~/.config/install/net-state-<host>-<ts>.txt   (0600, per-run history)
#   /mnt/ventoy/net-state-<host>.txt              (0644, latest; if mounted)
#
# Redaction: wpa-psk / wpa-password / psk= / password= / identity= values are
# replaced. SSID is masked in-place and fingerprinted (sha8) in the verdict,
# so the ifupdown stanza and the NM profile can be compared without revealing
# it; the plaintext stays readable on the machine (sudo cat
# /etc/network/interfaces). MAC addresses are masked globally.
#
# Deps: base-system bash + coreutils only (no iw/rfkill/nmcli required).
# Optional probes degrade to "unavailable: <tool>".
# ---------------------------------------------------------------------------
set -uo pipefail

# iw/rfkill live in /usr/sbin (not on a non-root PATH); the probes are
# unprivileged reads, so resolve and invoke them by path.
PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:$PATH"
export PATH

if (( $# )); then
    printf 'capture-network-state.sh: takes no arguments\n' >&2
    exit 2
fi

ETC="${NETSTATE_ETC:-/etc}"   # test hook only; bootstrap never sets it
STICK="/mnt/ventoy"

SUDO=()
PARTIAL=0
if command -v sudo >/dev/null 2>&1; then
    if [[ -t 0 ]]; then
        sudo -v >/dev/null 2>&1 || true
    fi
    if sudo -n true 2>/dev/null; then
        SUDO=(sudo -n)
    else
        PARTIAL=1
    fi
else
    PARTIAL=1
fi

asroot() {
    if (( ${#SUDO[@]} )); then "${SUDO[@]}" "$@"; else "$@"; fi
}
have() { command -v "$1" >/dev/null 2>&1; }
sect() { printf '\n=== %s ===\n' "$*"; }

redact() {
    sed -E \
        -e 's/^([[:space:]]*wpa-ssid)[[:space:]]+.*/\1 <ssid-masked>/' \
        -e 's/^([[:space:]]*ssid)=.*/\1=<ssid-masked>/' \
        -e 's/^([[:space:]]*wpa-psk)[[:space:]]+.*/\1 <redacted>/' \
        -e 's/^([[:space:]]*wpa-password)[[:space:]]+.*/\1 <redacted>/' \
        -e 's/^([[:space:]]*psk)=.*/\1=<redacted>/' \
        -e 's/^([[:space:]]*password)=.*/\1=<redacted>/' \
        -e 's/^([[:space:]]*identity)=.*/\1=<redacted>/'
}

mask_mac() {
    sed -E 's/(^|[^0-9A-Fa-f])([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}([^0-9A-Fa-f]|$)/\1<mac>\3/g'
}

ssid_of() {
    awk '
        /^[[:space:]]*wpa-ssid[[:space:]]+/ {
            line = $0
            sub(/^[[:space:]]*wpa-ssid[[:space:]]+/, "", line)
            sub(/[[:space:]]+$/, "", line)
            if (line ~ /^".*"$/) line = substr(line, 2, length(line) - 2)
            print line
            exit
        }'
}

mask_ssid() {
    local s="${1%$'\r'}"
    if [[ -z "$s" ]]; then
        printf '<none>'
        return 0
    fi
    printf '<ssid len=%d sha8=%s>' "${#s}" \
        "$(printf '%s' "$s" | sha256sum | cut -c1-8)"
}

# ── gather facts ──────────────────────────────────────────────────────────

interfaces="$ETC/network/interfaces"
iface_ssid="$(asroot cat -- "$interfaces" 2>/dev/null | ssid_of)"
if [[ -n "$iface_ssid" ]]; then stanza="yes"; else stanza="no"; fi
if (( ${#SUDO[@]} )); then sudo_state="available"; else sudo_state="unavailable"; fi
if (( PARTIAL == 1 )); then partial_bool=true; else partial_bool=false; fi

wifi_iface=""
for d in /sys/class/net/*; do
    [[ -e "$d/phy80211" ]] || continue
    wifi_iface="$(basename "$d")"
    break
done

driver="unavailable"
if [[ -n "$wifi_iface" ]]; then
    lnk="$(readlink -f "/sys/class/net/$wifi_iface/device/driver" 2>/dev/null || true)"
    [[ -n "$lnk" ]] && driver="$(basename "$lnk")"
fi

nm_dir="$ETC/NetworkManager/system-connections"
nm_files=()
if [[ -d "$nm_dir" ]]; then
    while IFS= read -r f; do
        nm_files+=("$f")
    done < <(asroot find "$nm_dir" -maxdepth 1 -name '*.nmconnection' -type f 2>/dev/null)
fi

# SSID: prefer the NM profile when NM owns the link, else the ifupdown
# stanza. Never printed in clear — only length + sha8 fingerprint.
nm_ssid=""
for f in "${nm_files[@]}"; do
    nm_ssid="$(asroot cat -- "$f" 2>/dev/null \
        | awk '/^ssid=/{print substr($0, 6); exit}')"
    [[ -n "$nm_ssid" ]] && break
done
if [[ -n "$nm_ssid" ]]; then
    ssid_raw="$nm_ssid"
    ssid_src="nm"
elif [[ -n "$iface_ssid" ]]; then
    ssid_raw="$iface_ssid"
    ssid_src="ifupdown"
else
    ssid_raw=""
    ssid_src="none"
fi
ssid_len=0
ssid_sha8=""
if [[ -n "$ssid_raw" ]]; then
    ssid_len=${#ssid_raw}
    ssid_sha8="$(printf '%s' "$ssid_raw" | sha256sum | cut -c1-8)"
fi
ssid_mask="$(mask_ssid "$ssid_raw")"

nm_wifi_state="unavailable"
if have nmcli; then
    nm_wifi_state="$(timeout 10 nmcli -t -f DEVICE,TYPE,STATE device status 2>/dev/null \
        | awk -F: -v i="$wifi_iface" '$1==i {print $3; exit}')"
    [[ -z "$nm_wifi_state" ]] && nm_wifi_state="absent"
fi

nm_installed=false
if have dpkg-query \
    && dpkg-query -W -f='${Status}' network-manager 2>/dev/null \
        | grep -q 'install ok installed'; then
    nm_installed=true
fi

owner="none"
if (( ${#nm_files[@]} > 0 )); then
    owner="nm"
elif [[ -n "$iface_ssid" ]]; then
    owner="ifupdown"
elif asroot cat -- "$interfaces" 2>/dev/null \
    | awk '$1=="iface" && $2!="lo" {f=1} END{exit !f}'; then
    owner="ifupdown-static"
fi

ts="$(date -u +%Y%m%dT%H%M%SZ)"
host="$(hostname -s 2>/dev/null || hostname 2>/dev/null || echo unknown)"
host="${host//[^A-Za-z0-9._-]/_}"
boot_id="$(cat /proc/sys/kernel/random/boot_id 2>/dev/null || echo unknown)"

localdir="${HOME:-/tmp}/.config/install"
mkdir -p "$localdir" 2>/dev/null || localdir="${HOME:-/tmp}"
out="$localdir/net-state-$host-$ts.txt"
tmp="$(mktemp 2>/dev/null)" || exit 0

# ── dump ──────────────────────────────────────────────────────────────────

{
    printf 'net-state capture — post-installer, pre-stage0\n'
    printf 'stage:   pre-stage0 (before apt / packages converge)\n'
    printf 'time:    %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf 'host:    %s\n' "$host"
    printf 'boot_id: %s\n' "$boot_id"
    printf 'kernel:  %s\n' "$(uname -r 2>/dev/null || echo unknown)"
    printf 'sudo:    %s\n' "$sudo_state"
    printf 'partial: %s\n' "$partial_bool"

    sect "verdict"
    printf 'owner=%s iface=%s driver=%s state=%s stanza=%s nm_profiles=%d\n' \
        "$owner" "${wifi_iface:-none}" "$driver" "$nm_wifi_state" \
        "$stanza" "${#nm_files[@]}"
    printf 'ssid=%s src=%s\n' "$ssid_mask" "$ssid_src"

    sect "os"
    cat "$ETC/os-release" 2>/dev/null || echo "absent: $ETC/os-release"

    sect "interfaces"
    printf 'path: %s\n' "$interfaces"
    printf 'stat: %s\n' \
        "$(stat -c 'mode=%a owner=%U:%G size=%s' "$interfaces" 2>/dev/null \
            || echo 'unreadable (need root)')"
    if asroot test -r "$interfaces" 2>/dev/null; then
        asroot cat -- "$interfaces" 2>/dev/null | redact
    else
        echo 'unreadable (sudo unavailable)'
    fi
    if [[ -d "$ETC/network/interfaces.d" ]]; then
        for f in "$ETC"/network/interfaces.d/*; do
            [[ -f "$f" ]] || continue
            printf '\n--- %s\n' "$f"
            if asroot test -r "$f" 2>/dev/null; then
                asroot cat -- "$f" 2>/dev/null | redact
            else
                echo 'unreadable (sudo unavailable)'
            fi
        done
    else
        echo "absent: $ETC/network/interfaces.d"
    fi

    sect "NetworkManager"
    printf 'installed=%s enabled=%s active=%s\n' \
        "$nm_installed" \
        "$(systemctl is-enabled NetworkManager 2>/dev/null || echo unknown)" \
        "$(systemctl is-active NetworkManager 2>/dev/null || echo unknown)"
    printf 'profiles=%d\n' "${#nm_files[@]}"
    if (( ${#nm_files[@]} == 0 )); then
        echo '(no .nmconnection files)'
    else
        for f in "${nm_files[@]}"; do
            printf '\n--- %s\n' "$f"
            printf 'stat: %s\n' \
                "$(stat -c 'mode=%a owner=%U:%G size=%s' "$f" 2>/dev/null \
                    || echo unreadable)"
            if asroot test -r "$f" 2>/dev/null; then
                asroot cat -- "$f" 2>/dev/null | redact
            else
                echo 'unreadable (sudo unavailable)'
            fi
        done
    fi
    printf '\n--- %s/NetworkManager.conf\n' "$ETC"
    asroot cat -- "$ETC/NetworkManager/NetworkManager.conf" 2>/dev/null \
        | redact || echo 'absent'
    if [[ -d "$ETC/NetworkManager/conf.d" ]]; then
        for f in "$ETC"/NetworkManager/conf.d/*; do
            [[ -f "$f" ]] || continue
            printf '\n--- %s\n' "$f"
            cat -- "$f" 2>/dev/null
        done
    fi

    sect "ifupdown"
    printf 'networking.service: enabled=%s active=%s\n' \
        "$(systemctl is-enabled networking 2>/dev/null || echo unknown)" \
        "$(systemctl is-active networking 2>/dev/null || echo unknown)"
    printf 'ifupdown: %s\n' \
        "$(dpkg-query -W -f='${Version} ${Status}' ifupdown 2>/dev/null \
            || echo 'not installed')"

    sect "resolv.conf"
    ls -l "$ETC/resolv.conf" 2>/dev/null || echo "absent: $ETC/resolv.conf"
    cat "$ETC/resolv.conf" 2>/dev/null || echo 'unreadable'
    if [[ -f /run/systemd/resolve/resolv.conf ]]; then
        printf '\n--- /run/systemd/resolve/resolv.conf\n'
        cat /run/systemd/resolve/resolv.conf 2>/dev/null
    fi
    if have resolvectl; then
        printf '\n--- resolvectl status\n'
        timeout 10 resolvectl status 2>/dev/null | head -40
    else
        echo 'unavailable: resolvectl'
    fi

    sect "links and addresses"
    if have ip; then
        ip -d addr 2>&1
    else
        echo 'unavailable: ip'
    fi

    sect "routes"
    if have ip; then
        ip route 2>&1
        ip -6 route 2>&1
    else
        echo 'unavailable: ip'
    fi

    sect "wifi"
    printf 'iface: %s\n' "${wifi_iface:-none}"
    if [[ -n "$wifi_iface" ]]; then
        printf '\n--- /proc/net/wireless\n'
        cat /proc/net/wireless 2>/dev/null || echo 'absent'
    fi
    if have nmcli; then
        printf '\n--- nmcli device status\n'
        timeout 10 nmcli -t -f DEVICE,TYPE,STATE device status 2>&1 || true
        printf '\n--- nmcli connection show\n'
        timeout 10 nmcli -t -f NAME,TYPE,DEVICE connection show 2>&1 || true
        if [[ -n "$wifi_iface" ]]; then
            printf '\n--- nmcli device show %s\n' "$wifi_iface"
            timeout 10 nmcli -t -f all device show "$wifi_iface" 2>&1 || true
        fi
    else
        echo 'unavailable: nmcli'
    fi
    if have iw; then
        printf '\n--- iw dev\n'
        timeout 10 iw dev 2>&1 || true
        printf '\n--- iw reg get\n'
        timeout 10 iw reg get 2>&1 || true
    else
        echo 'unavailable: iw'
    fi
    if have rfkill; then
        printf '\n--- rfkill list\n'
        timeout 10 rfkill list 2>&1 || true
    else
        echo 'unavailable: rfkill'
    fi

    sect "processes"
    ps -eo pid,comm,args 2>/dev/null \
        | grep -E 'wpa_supplicant|dhclient|dhcpcd|NetworkManager' \
        | grep -v grep || echo '(none)'

    sect "modules and firmware"
    if have lsmod; then
        lsmod 2>/dev/null \
            | grep -E '^(cfg80211|mac80211|iwlwifi|iwlmvm|rtw|rtl|mt7|mt76|ath|brcm)' \
            || echo '(no wifi modules loaded)'
    else
        echo 'unavailable: lsmod'
    fi
    if have lspci; then
        printf '\n--- lspci -nnk (network)\n'
        timeout 10 lspci -nnk 2>/dev/null \
            | grep -A3 -iE 'network|wireless' || echo '(no pci net device)'
    else
        echo 'unavailable: lspci'
    fi
    printf '\n--- dmesg (firmware/wifi tail)\n'
    asroot dmesg 2>/dev/null \
        | grep -iE 'firmware|wlan|wifi|iwlwifi|80211' | tail -40 \
        || echo '(dmesg unavailable)'

    sect "wpa_supplicant"
    if [[ -f "$ETC/wpa_supplicant/wpa_supplicant.conf" ]]; then
        asroot cat -- "$ETC/wpa_supplicant/wpa_supplicant.conf" 2>/dev/null \
            | redact
    else
        echo "absent: $ETC/wpa_supplicant/wpa_supplicant.conf"
    fi

    sect "journal (NetworkManager, last 40)"
    if have journalctl; then
        journalctl -b --no-pager -n 40 -u NetworkManager 2>&1 || true
    else
        echo 'unavailable: journalctl'
    fi

    sect "packages"
    for p in network-manager wpasupplicant ifupdown iw wireless-tools rfkill netcfg; do
        printf '%-20s %s\n' "$p" \
            "$(dpkg-query -W -f='${Version} ${Status}' "$p" 2>/dev/null \
                || echo 'not installed')"
    done

    sect "installer evidence"
    if [[ -d /var/log/installer ]]; then
        asroot ls -1 /var/log/installer 2>/dev/null || true
        printf '\n--- syslog (net lines, tail 40)\n'
        if asroot test -r /var/log/installer/syslog 2>/dev/null; then
            asroot cat -- /var/log/installer/syslog 2>/dev/null \
                | grep -iE 'netcfg|wlan|wifi|wpa|dhcp' | tail -40 \
                || echo '(no matching lines)'
        else
            echo 'unreadable (sudo unavailable)'
        fi
        for f in hardware-summary lsb-release; do
            [[ -e "/var/log/installer/$f" ]] || continue
            printf '\n--- %s\n' "$f"
            asroot cat -- "/var/log/installer/$f" 2>/dev/null | head -40
        done
    else
        echo 'absent: /var/log/installer (not a d-i installed system?)'
    fi

    sect "machine-readable"
    printf 'NETSTATE {"host":"%s","boot_id":"%s","owner":"%s","iface":"%s",' \
        "$host" "$boot_id" "$owner" "${wifi_iface:-none}"
    printf '"ssid_src":"%s","ssid_len":%d,"ssid_sha8":"%s",' \
        "$ssid_src" "$ssid_len" "$ssid_sha8"
    printf '"driver":"%s","wifi_state":"%s","stanza":%s,"nm_profiles":%d,' \
        "$driver" "$nm_wifi_state" \
        "$([[ $stanza == "yes" ]] && echo true || echo false)" "${#nm_files[@]}"
    printf '"nm_installed":%s,"sudo":"%s","partial":%s}\n' \
        "$nm_installed" "$sudo_state" "$partial_bool"
} > "$tmp" 2>&1

mask_mac < "$tmp" > "$tmp.masked" 2>/dev/null && mv -f "$tmp.masked" "$tmp"
rm -f "$tmp.masked"

chmod 600 "$tmp" 2>/dev/null || true
if ! mv -f "$tmp" "$out" 2>/dev/null; then
    printf 'capture-network-state.sh: cannot write %s\n' "$out" >&2
    exit 0
fi
chmod 600 "$out" 2>/dev/null || true

stick_out=""
if mountpoint -q "$STICK" 2>/dev/null; then
    if (( ${#SUDO[@]} )) \
        && "${SUDO[@]}" install -m 0644 "$out" "$STICK/net-state-$host.txt" 2>/dev/null; then
        stick_out="$STICK/net-state-$host.txt"
    elif cp -f "$out" "$STICK/net-state-$host.txt" 2>/dev/null \
        && chmod 0644 "$STICK/net-state-$host.txt" 2>/dev/null; then
        stick_out="$STICK/net-state-$host.txt"
    fi
fi

printf 'network state captured: %s\n' "$out"
[[ -n "$stick_out" ]] && printf 'stick copy: %s\n' "$stick_out"
printf 'verdict: owner=%s iface=%s driver=%s state=%s stanza=%s nm_profiles=%d\n' \
    "$owner" "${wifi_iface:-none}" "$driver" "$nm_wifi_state" "$stanza" \
    "${#nm_files[@]}"
exit 0
