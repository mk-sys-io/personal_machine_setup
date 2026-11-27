#!/usr/bin/env bash
set -euo pipefail

# ---------------------------------------------------------------------------
# refindplus-assert-bootorder — re-assert the RefindPlus NVRAM boot entry and
# verify/repair the ESP loaders the boot chain depends on
#
# Symptom-based self-heal: does NOT detect what demoted the entry, only
# whether the RefindPlus entry exists / is healthy / is first in BootOrder,
# and fixes that. Invoked from root contexts only (systemd system unit
# defaults to User=root; apt DPkg::Post-Invoke hooks inherit apt's root
# privileges) — no sudo inside. Never fails: every error path logs and
# exits 0 (a failing apt hook would per man apt.conf abort apt; a failing
# boot unit would pile errors into the journal on every boot).
#
# Triggers (deployed by install.py step 62):
#   - refindplus-bootorder.service — oneshot at every boot. Heals this
#     board's firmware promoting the last-booted entry over RefindPlus.
#   - refindplus-shutdown-verify.service — before shutdown/umount. Catches
#     manual grub-install and other unhooked writers while the system runs.
#   - refindplus-esp-verify.timer — daily. Catches long uptimes and drift.
#   - /etc/apt/apt.conf.d/99-refindplus-bootorder — after every dpkg
#     transaction. Heals grub/shim package upgrades (their postinst runs
#     grub-install, which puts the debian entry first).
#   - Every trigger also verifies and repairs the ESP loaders (size,
#     SHA-256, PE header), because grub-install can exit 0 after writing
#     a truncated file.
#
# Residual: a loader corrupted between triggers (e.g. by a manual
# grub-install that the shutdown unit never ran because of a crash) is
# repaired by the next trigger. Routine sources are healed before any reboot
# can consume them.
# ---------------------------------------------------------------------------

LABEL="RefindPlus Boot Manager"
LOADER="/EFI/refind/refind_x64.efi"
ESP="/boot/efi"

log() { logger -t refindplus-bootorder "$*" 2>/dev/null || true; }

# --- ESP loader verification -------------------------------------------------
# grub-install reports success without checking what landed on the ESP: its
# copy loop treats a zero-length read/write as EOF, and nothing compares the
# written file with its source afterwards. A 4096-byte shimx64.efi survived a
# routine apt upgrade on 2026-11-25 and broke every Debian boot path. Verify
# each loader against its authoritative source and repair it in place; this
# must never make the caller fail (a failing apt hook aborts apt).

ESP_STATE="/var/lib/refindplus"
DIGESTS="$ESP_STATE/esp-digests"
FAIL_MARKER="$ESP_STATE/verify-failed"

# Package-owned loaders: the package file is authoritative and repairable.
declare -A LOADER_SOURCE=(
    ["EFI/debian/shimx64.efi"]="/usr/lib/shim/shimx64.efi.signed"
    ["EFI/debian/mmx64.efi"]="/usr/lib/shim/mmx64.efi.signed"
    ["EFI/debian/grubx64.efi"]="/usr/lib/grub/x86_64-efi-signed/grubx64.efi.signed"
)

# Always checked, even when NVRAM names something else.
STANDARD_LOADERS=(
    "EFI/debian/shimx64.efi"
    "EFI/debian/mmx64.efi"
    "EFI/debian/grubx64.efi"
    "EFI/refind/refind_x64.efi"
)

# ledger_get REL — print the recorded digest; nonzero when absent.
ledger_get() {
    [[ -r "$DIGESTS" ]] || return 1
    awk -v p="$1" 'tolower($1) == tolower(p) { print $2; found = 1 } END { exit !found }' "$DIGESTS"
}

# ledger_put REL DIGEST SIZE — replace this path's line, keep the rest.
ledger_put() {
    local tmp
    tmp="$(mktemp "$ESP_STATE/.digests.XXXXXX" 2>/dev/null)" || { log "ERROR: mktemp failed — digest not recorded"; return 0; }
    if [[ -r "$DIGESTS" ]]; then
        awk -v p="$1" 'tolower($1) != tolower(p)' "$DIGESTS" > "$tmp" 2>/dev/null || true
    fi
    printf '%s\t%s\t%s\n' "$1" "$2" "$3" >> "$tmp"
    mv -f "$tmp" "$DIGESTS" 2>/dev/null || rm -f "$tmp"
}

# pe_header_ok FILE — MZ at 0 and PE\0\0 at e_lfanew. Not a substitute for a
# size check: the first 4096 bytes of a truncated loader still pass this.
pe_header_ok() {
    local f="$1" lfanew
    [[ "$(od -An -tx1 -N2 -- "$f" 2>/dev/null | tr -d ' \n')" == "4d5a" ]] || return 1
    lfanew="$(od -An -tu4 -j60 -N4 -- "$f" 2>/dev/null | tr -d ' \n')"
    [[ "$lfanew" =~ ^[0-9]+$ ]] || return 1
    [[ "$(od -An -tx1 -j"$lfanew" -N4 -- "$f" 2>/dev/null | tr -d ' \n')" == "50450000" ]]
}

# verify_one REL — 0 when the loader is intact or was repaired.
verify_one() {
    local rel="$1" abs="$ESP/$1" src want got size
    src="${LOADER_SOURCE[$rel]:-}"
    if [[ -n "$src" && -f "$src" ]]; then
        want="$(sha256sum -- "$src" 2>/dev/null | cut -d' ' -f1 || true)"
    else
        want="$(ledger_get "$rel" 2>/dev/null || true)"
    fi

    if [[ -f "$abs" ]]; then
        size="$(stat -c %s -- "$abs" 2>/dev/null || echo 0)"
        got="$(sha256sum -- "$abs" 2>/dev/null | cut -d' ' -f1 || true)"
        if [[ -n "$got" && -n "$want" && "$got" == "$want" ]] && pe_header_ok "$abs"; then
            return 0
        fi
        # No source and no recorded digest: trust a plausible first sighting.
        if [[ -z "$want" && -n "$got" && "$size" -ge 65536 ]] && pe_header_ok "$abs"; then
            ledger_put "$rel" "$got" "$size"
            log "NOTE: recorded first-seen digest for /$rel"
            return 0
        fi
        log "ERROR: loader mismatch: /$rel (${size} bytes, sha256=${got:-unreadable})"
    else
        log "ERROR: loader missing: /$rel"
    fi

    if [[ -n "$src" && -f "$src" ]]; then
        if cp -f -- "$src" "$abs" 2>/dev/null && sync -- "$abs"; then
            got="$(sha256sum -- "$abs" 2>/dev/null | cut -d' ' -f1 || true)"
            if [[ -n "$got" && "$got" == "$want" ]] && pe_header_ok "$abs"; then
                log "REPAIRED: /$rel restored from $src"
                return 0
            fi
        fi
        log "ERROR: repair failed: /$rel"
    else
        log "ERROR: no authoritative source for /$rel — manual repair required"
    fi
    return 1
}

# nvram_loaders — ESP-relative paths named by NVRAM entries (debian/refind).
nvram_loaders() {
    local p
    while IFS= read -r p; do
        p="${p//\\//}"
        case "$p" in
            EFI/debian/*|EFI/refind/*) printf '%s\n' "$p" ;;
        esac
    done < <(sed -n 's/.*File(\\\([^)]*\))/\1/p' <<<"$state" 2>/dev/null)
}

verify_loaders() {
    local rel failed=0
    local -a paths=()
    if ! findmnt -no SOURCE "$ESP" >/dev/null 2>&1; then
        log "ESP not mounted at $ESP — skipping loader verification"
        return 0
    fi
    mkdir -p "$ESP_STATE" 2>/dev/null || true
    while IFS= read -r rel; do
        [[ -z "$rel" ]] && continue
        [[ "$rel" != /* ]] || continue
        case "/$rel/" in
            *"/../"*|*"/./"*) continue ;;
        esac
        paths+=("$rel")
    done < <(nvram_loaders)
    paths+=("${STANDARD_LOADERS[@]}")
    mapfile -t paths < <(printf '%s\n' "${paths[@]}" | sort -u)
    for rel in "${paths[@]}"; do
        verify_one "$rel" || failed=1
    done
    if [[ "$failed" -eq 1 ]]; then
        date -Is > "$FAIL_MARKER" 2>/dev/null || true
        log "ERROR: ESP loader verification failed — see $FAIL_MARKER"
    else
        rm -f "$FAIL_MARKER" 2>/dev/null || true
    fi
    return 0
}

# Single-instance serialization. The three triggers (boot unit, apt hook,
# module run) can overlap, and concurrent NVRAM access has produced
# contradictory reads in the field (healthy → stale → missing within one
# second, 2026-10-24 18:00). Non-blocking: an overlapping instance exits 0
# immediately and lets the holder do the work.
exec 9>/run/lock/refindplus-bootorder.lock || exit 0
if ! flock -n 9; then
    log "skipped: another instance holds the lock (ppid=$PPID)"
    exit 0
fi

# Caller transparency: every run names its parent process, so an unexpected
# trigger source identifies itself in the journal instead of staying a
# mystery to reconstruct after the fact.
log "trigger fired: ppid=$PPID pcomm=$(cat "/proc/$PPID/comm" 2>/dev/null || echo '?')"

get_state() {
    state="$(efibootmgr -v 2>/dev/null || true)"
}

# locate — extract entry number + HD GUID for $LABEL from $state.
locate() {
    entry_num=""
    entry_guid=""
    local line
    line="$(grep -iF "$LABEL" <<<"$state" | head -1 || true)"
    [[ -z "$line" ]] && return 0
    entry_num="$(sed -n 's/^Boot\([0-9a-fA-F]\{4\}\)[ *].*/\1/p' <<<"$line")"
    entry_guid="$(sed -n 's/.*HD([0-9]*,GPT,\([0-9a-fA-F-]*\),.*/\1/p' <<<"$line")"
}

# resolv_esp — set disk / part / live_guid for the mounted ESP; empty on failure.
resolv_esp() {
    disk=""
    part=""
    live_guid=""
    local dev
    dev="$(findmnt -no SOURCE "$ESP" 2>/dev/null || true)"
    [[ -n "$dev" ]] || return 0
    disk="$(lsblk -no PKNAME "$dev" 2>/dev/null | head -1 || true)"
    part="$(lsblk -no PARTN "$dev" 2>/dev/null | head -1 || true)"
    live_guid="$(lsblk -no PARTUUID "$dev" 2>/dev/null | head -1 || true)"
}

# create — new NVRAM entry; returns nonzero on failure (caller in if-guard).
create() {
    if ! efibootmgr --create --disk "/dev/$disk" --part "$part" \
        --loader "$LOADER" --label "$LABEL" >/dev/null 2>&1; then
        log "ERROR: efibootmgr --create failed"
        return 1
    fi
}

get_state
verify_loaders
if [[ -z "$state" ]]; then
    log "efibootmgr returned no state — not a UEFI system? nothing to do"
    exit 0
fi

# Defensive: BootNext is one-shot per UEFI spec — spec-compliant firmware
# deletes it after use, so any BootNext visible here has lingered and would
# hijack the next boot past RefindPlus. Known trade-off: a fwupd capsule
# update that set BootNext just before this runs could be lost (fwupd
# retries later); accepted as the lesser harm vs a hijacked boot.
if grep -q '^BootNext' <<<"$state"; then
    efibootmgr -N >/dev/null 2>&1 || true
    log "cleared stale BootNext"
fi

locate
resolv_esp

entry_ok=0
if [[ -z "$entry_num" ]]; then
    if [[ -n "$disk" && -n "$part" ]]; then
        if create; then
            get_state
            locate
            if [[ -n "$entry_num" ]]; then
                entry_ok=1
                log "RefindPlus NVRAM entry missing — created (boot $entry_num)"
            fi
        fi
    else
        log "RefindPlus entry missing and ESP not resolvable — cannot create"
        exit 0
    fi
else
    if [[ -n "$entry_guid" && -n "$live_guid" && "${entry_guid,,}" == "${live_guid,,}" ]]; then
        entry_ok=1
    elif [[ -n "$disk" && -n "$part" ]]; then
        # Stale device path (partition GUID changed): delete + recreate.
        efibootmgr -b "$entry_num" -B >/dev/null 2>&1 || log "delete of stale entry failed"
        if create; then
            get_state
            locate
            if [[ -n "$entry_num" ]]; then
                entry_ok=1
                log "RefindPlus NVRAM entry stale — recreated (boot $entry_num)"
            fi
        fi
    else
        log "RefindPlus entry device path stale and ESP not resolvable — cannot recreate"
    fi
fi

if [[ "$entry_ok" != 1 ]]; then
    log "RefindPlus entry not present/healthy — leaving BootOrder untouched"
    exit 0
fi

order="$(sed -n 's/^BootOrder: //p' <<<"$state" | head -1 || true)"
if [[ -z "$order" ]]; then
    log "BootOrder not readable — leaving as is"
    exit 0
fi

first="${order%%,*}"
if [[ "${first,,}" == "${entry_num,,}" ]]; then
    exit 0
fi

rest=""
IFS=',' read -ra items <<<"$order"
for item in "${items[@]}"; do
    [[ "${item,,}" == "${entry_num,,}" ]] && continue
    rest+="${item},"
done
rest="${rest%,}"

if ! efibootmgr -o "${entry_num},${rest}" >/dev/null 2>&1; then
    log "ERROR: efibootmgr -o failed (wanted ${entry_num},${rest})"
    exit 0
fi
log "BootOrder re-asserted: RefindPlus ($entry_num) first"

exit 0
