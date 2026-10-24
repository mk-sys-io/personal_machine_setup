#!/usr/bin/env bash
set -euo pipefail

# ---------------------------------------------------------------------------
# refindplus-assert-bootorder — re-assert the RefindPlus NVRAM boot entry
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
#   - /etc/apt/apt.conf.d/99-refindplus-bootorder — after every dpkg
#     transaction. Heals grub/shim package upgrades (their postinst runs
#     grub-install, which puts the debian entry first).
#
# Residual: heals only at trigger time (boot / dpkg transaction). A coup
# from an unhooked source (manual grub-install, clone tool) stands until
# the next boot — one boot through grub, then the unit re-asserts.
# Routine sources (firmware promotion, apt upgrades) are healed before
# any reboot can consume them.
# ---------------------------------------------------------------------------

LABEL="RefindPlus Boot Manager"
LOADER="/EFI/refind/refind_x64.efi"
ESP="/boot/efi"

log() { logger -t refindplus-bootorder "$*" 2>/dev/null || true; }

# Single-instance serialization. The three triggers (boot unit, apt hook,
# module run) can overlap, and concurrent NVRAM access has produced
# contradictory reads in the field (healthy → stale → missing within one
# second, 2026-10-24 18:00). Non-blocking: an overlapping instance exits 0
# immediately and lets the holder do the work.
exec 9>/run/lock/refindplus-bootorder.lock
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
