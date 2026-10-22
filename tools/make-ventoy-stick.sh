#!/usr/bin/env bash
# tools/make-ventoy-stick.sh — build the Debian install stick (builder-side).
#
# Implements the seven manual-layout steps from docs/recovery/bootstrap-wifi.md
# ("Stick-builder fallback") one-for-one, so script and doc cannot drift:
# wipe-confirm /dev/sdX → Ventoy2Disk.sh → verify label → ISO →
# copy stick-root files → verify listing.
#
# Runs on the BUILDER (live iron with /opt/ventoy from the 20-packages
# converge); never on blank iron. DESTRUCTIVE to the target USB: all
# data on it is wiped after an explicit type-YES confirm.
set -euo pipefail

# Config-driven REPO_ROOT (mirrors tools/vm.py): deployed copy reads
# ~/.config/linux_setup/config.txt (REPO_ROOT derived at `make dotfiles`
# time, never stored in committed config.txt); in-tree fallback only.
get_repo_root() {
    local cfg="$HOME/.config/linux_setup/config.txt" line val
    if [[ -f "$cfg" ]]; then
        while IFS= read -r line || [[ -n "$line" ]]; do
            line="${line#"${line%%[![:space:]]*}"}"
            line="${line%"${line##*[![:space:]]}"}"
            [[ -z "$line" || "${line:0:1}" == "#" ]] && continue
            [[ "$line" != REPO_ROOT=* ]] && continue
            val="${line#REPO_ROOT=}"
            val="${val#"${val%%[![:space:]]*}"}"
            val="${val%"${val##*[![:space:]]}"}"
            val="${val%\"}"; val="${val#\"}"; val="${val%\'}"; val="${val#\'}"
            if [[ -n "$val" && -f "$val/lib/helpers/common.sh" ]]; then
                printf '%s\n' "$val"
                return 0
            fi
        done < "$cfg"
    fi
    local fallback
    fallback="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
    if [[ -f "$fallback/lib/helpers/common.sh" ]]; then
        printf '%s\n' "$fallback"
        return 0
    fi
    echo "error: REPO_ROOT missing or without lib/helpers/common.sh." >&2
    echo "  Run 'make dotfiles' (or 'make all') to deploy ~/.config/linux_setup/config.txt." >&2
    return 1
}
REPO_ROOT="$(get_repo_root)" || exit 1
export REPO_ROOT
# shellcheck source=../lib/helpers/common.sh
source "$REPO_ROOT/lib/helpers/common.sh"

STICK_LABEL="Ventoy"
STICK_MOUNT="/mnt/ventoy"
VENTOY_MARKER="/opt/ventoy/.installed-version"
VENTOY_DIR=""
VENTOY_VER=""
# Ventoy reserves ~33 MiB (1 MiB start + 32 MiB EFI); 64 MiB also covers
# filesystem/metadata overhead. Pre-wipe gate for the ISO data partition.
ISO_DISK_MARGIN_BYTES=$((64 * 1024 * 1024))

# ---------------------------------------------------------------------------
# ISO discovery — tiered search.
# Tier-1 scans ~/ISOs + ~/Downloads only (instant). A full-home search is
# opt-in, never default: an unpruned walk takes ~40s+ on a lived-in home
# and surfaces fixture junk (e.g. *.iso testdata under ~/go module caches).
# ~/ISOs is conventional (backup-excluded per 40-backup.md) but not
# guaranteed — hence Downloads alongside, never instead.
# ---------------------------------------------------------------------------
tier1_isos() {
    shopt -s nullglob
    local files=("$HOME"/ISOs/*.iso "$HOME"/Downloads/*.iso)
    shopt -u nullglob
    (( ${#files[@]} == 0 )) && return 0
    # shellcheck disable=SC2012
    ls -t -- "${files[@]}"
}

valid_iso() {
    # -L: follow symlinks (file(1) otherwise reports "symbolic link to …").
    [[ -f "$1" && "$1" == *.iso ]] && file -bL "$1" | grep -qi 'iso 9660'
}

scan_home_isos() {
    log "Scanning home (pruned: .cache, go, node_modules, .git) — may take ~1 min ..."
    find "$HOME" \
        \( -path "$HOME/.cache/*" -o -path "$HOME/go/*" \
           -o -path "*/node_modules/*" -o -path "*/.git/*" \) -prune \
        -o -type f -name '*.iso' -print 2>/dev/null
}

# Prints the chosen ISO path on stdout; all human-facing text goes to
# stderr (callers capture stdout via $(...), which would swallow menus).
pick_iso() {
    local candidates=() choice iso
    mapfile -t candidates < <(tier1_isos)
    if (( ${#candidates[@]} > 0 )); then
        echo "Found ISOs (newest first):" >&2
        local i=1 c mark
        for c in "${candidates[@]}"; do
            mark=" "
            [[ "$(basename "$c")" == *netinst* ]] && mark="*"
            printf '  %d)%s %s\n' "$i" "$mark" "$c" >&2
            (( i++ ))
        done
        echo "  (* = netinst match)" >&2
    else
        echo "No ISOs in ~/ISOs or ~/Downloads." >&2
    fi
    echo "Options: <number> | p = another path | s = scan home | q = quit" >&2
    read -rp "ISO choice: " choice
    case "$choice" in
        q|Q) return 1 ;;
        p|P)
            read -rp "ISO path (absolute, e.g. /home/mike/Downloads/debian-13.7.0-amd64-netinst.iso): " iso
            # Expand leading ~, resolve relative against cwd, require absolute.
            # shellcheck disable=SC2088
            if [[ "$iso" == "~" ]]; then
                iso="$HOME"
            elif [[ "$iso" == "~/"* ]]; then
                iso="$HOME/${iso#'~/'}"
            fi
            if [[ -n "${iso:-}" && "$iso" != /* ]]; then
                if command -v realpath >/dev/null 2>&1; then
                    iso="$(realpath -m "$iso")"
                elif command -v readlink >/dev/null 2>&1; then
                    iso="$(readlink -m "$iso")"
                fi
            fi
            if [[ -z "${iso:-}" || "$iso" != /* ]]; then
                log_error "Need an absolute path to the .iso (got: ${iso:-<empty>})."
                return 1
            fi
            ;;
        s|S)
            mapfile -t candidates < <(scan_home_isos)
            if (( ${#candidates[@]} == 0 )); then
                log_error "Home scan found nothing."
                return 1
            fi
            local j=1
            for c in "${candidates[@]}"; do
                printf '  %d) %s\n' "$j" "$c" >&2
                (( j++ ))
            done
            read -rp "ISO number: " choice
            if [[ "$choice" =~ ^[0-9]+$ ]] \
                && (( choice >= 1 && choice <= ${#candidates[@]} )); then
                iso="${candidates[$(( choice - 1 ))]}"
            else
                log_error "Invalid choice."
                return 1
            fi
            ;;
        *)
            if [[ "$choice" =~ ^[0-9]+$ ]] \
                && (( choice >= 1 && choice <= ${#candidates[@]} )); then
                iso="${candidates[$(( choice - 1 ))]}"
            else
                log_error "Invalid choice."
                return 1
            fi
            ;;
    esac
    if [[ -z "${iso:-}" ]] || ! valid_iso "$iso"; then
        log_error "Not a readable ISO 9660 image: ${iso:-<empty>}."
        return 1
    fi
    printf '%s\n' "$iso"
}

# ---------------------------------------------------------------------------
# Device handling
# ---------------------------------------------------------------------------
# disk_transport <node> — prints whole-disk|TRAN|SUBSYSTEMS for a device
# or partition (partitions resolve via PKNAME). Empty fields on lookup fail.
disk_transport() {
    local node="$1" disk tran subs
    disk="$(lsblk -dno PKNAME "$node" 2>/dev/null | tr -d '[:space:]')"
    [[ -n "$disk" ]] && node="/dev/$disk"
    tran="$(lsblk -dn -o TRAN "$node" 2>/dev/null | tr -d '[:space:]')"
    subs="$(lsblk -dn -o SUBSYSTEMS "$node" 2>/dev/null | tr -d '[:space:]')"
    printf '%s|%s|%s\n' "$node" "$tran" "$subs"
}

# disk_content <whole-disk-node> — one-line wipe-regret summary for the
# pick table (first formatted child partition). Read-only lsblk probe:
# crypto_LUKS members report opaque, unmounted vfat/exFAT shows no
# usage (kernel exposes it only when mounted) — displayed as-is, never
# implied empty.
disk_content() {
    local node="$1" disk line name fstype label used
    disk="$(basename "$node")"
    while IFS= read -r line || [[ -n "$line" ]]; do
        if [[ "$line" =~ NAME=\"([^\"]*)\" ]]; then
            name="${BASH_REMATCH[1]}"
        else
            continue
        fi
        [[ "$name" == "$disk" ]] && continue
        [[ "$line" =~ FSTYPE=\"([^\"]*)\" ]] && fstype="${BASH_REMATCH[1]}" || fstype=""
        [[ -z "$fstype" ]] && continue
        if [[ "$fstype" == "crypto_LUKS" ]]; then
            printf 'crypto_LUKS (encrypted) — opaque'
            return 0
        fi
        [[ "$line" =~ LABEL=\"([^\"]*)\" ]] && label="${BASH_REMATCH[1]}" || label=""
        [[ "$line" =~ FSUSED=\"([^\"]*)\" ]] && used="${BASH_REMATCH[1]}" || used=""
        if [[ -n "$label" && -n "$used" ]]; then
            printf "%s '%s' %s used" "$fstype" "$label" "$used"
        elif [[ -n "$label" ]]; then
            printf "%s '%s'" "$fstype" "$label"
        elif [[ -n "$used" ]]; then
            printf '%s %s used' "$fstype" "$used"
        else
            printf '%s' "$fstype"
        fi
        return 0
    done < <(lsblk -P -o NAME,FSTYPE,LABEL,UUID,FSUSED "$node" 2>/dev/null)
    printf 'no partitions'
}

# device_detail <whole-disk-node> — confirm-screen detail, one line per
# formatted child partition (stderr; transcript gets the same via log
# only for the chosen device in main). Read-only, LUKS-safe.
device_detail() {
    local dev="$1" disk line name fstype label uuid used
    disk="$(basename "$dev")"
    while IFS= read -r line || [[ -n "$line" ]]; do
        if [[ "$line" =~ NAME=\"([^\"]*)\" ]]; then
            name="${BASH_REMATCH[1]}"
        else
            continue
        fi
        [[ "$name" == "$disk" ]] && continue
        [[ "$line" =~ FSTYPE=\"([^\"]*)\" ]] && fstype="${BASH_REMATCH[1]}" || fstype=""
        [[ -z "$fstype" ]] && continue
        if [[ "$fstype" == "crypto_LUKS" ]]; then
            printf '    %s  crypto_LUKS (encrypted member — preview unavailable)\n' "$name" >&2
            continue
        fi
        [[ "$line" =~ LABEL=\"([^\"]*)\" ]] && label="${BASH_REMATCH[1]}" || label=""
        [[ "$line" =~ UUID=\"([^\"]*)\" ]] && uuid="${BASH_REMATCH[1]}" || uuid=""
        [[ "$line" =~ FSUSED=\"([^\"]*)\" ]] && used="${BASH_REMATCH[1]}" || used=""
        printf '    %s  %s  label=%s uuid=%s used=%s\n' \
            "$name" "$fstype" "${label:-—}" "${uuid:-—}" "${used:-—}" >&2
    done < <(lsblk -P -o NAME,FSTYPE,LABEL,UUID,FSUSED "$dev" 2>/dev/null)
}

# NOTE: there is deliberately no pre-wipe file viewer. The wipe decision
# needs attribution (size/model/label/content, all probed read-only
# above), never file browsing — mounting a foreign filesystem would run
# its parser code in-kernel and risk journal replay. The only viewer is
# the post-build listing in main(), which reads our own mount.

# dev_present <whole-disk-node> — liveness gate against physical yank
# (bumped stick, cut connection). Called after the number pick (kills
# ghost-device confirms) and right before the Ventoy invocation (closes
# the confirm-to-y window). Deliberate non-goals: no udev watch, no
# resume/partial-write recovery — a disturbed write is always fixed by
# rebuilding (-I reinstalls clean), and every I/O error below says so.
dev_present() {
    [[ -b "$1" ]] && lsblk -dn "$1" >/dev/null 2>&1
}

# dev_size <whole-disk-node> — sectors from sysfs; empty when unreadable.
dev_size() {
    cat "/sys/class/block/$(basename "$1")/size" 2>/dev/null
}

# dev_media_loaded <whole-disk-node> — present AND reporting non-zero
# capacity. Candidate selection and both pre-install gates use this: a 0 B
# node is the post-eject state of some USB bridges and cannot be written.
dev_media_loaded() {
    local size
    dev_present "$1" || return 1
    size="$(dev_size "$1")"
    [[ "$size" =~ ^[0-9]+$ && "$size" != "0" ]]
}

# has_iso_capacity <disk-bytes> <iso-bytes> — pre-wipe capacity gate.
# iso_bytes 0 (unknown) skips the check; the post-mount ENOSPC gate remains.
has_iso_capacity() {
    local disk_bytes="$1" iso_bytes="$2"
    (( iso_bytes == 0 || disk_bytes >= iso_bytes + ISO_DISK_MARGIN_BYTES ))
}

# dev_ejected <whole-disk-node> — true once safe to unplug: the node is
# gone OR its media is unloaded (size 0). `sudo eject` on some USB
# bridges leaves the whole-disk node behind at 0 B, so dev_present alone
# is NOT a valid eject-success test.
dev_ejected() {
    dev_present "$1" || return 0
    [[ "$(dev_size "$1")" == "0" ]]
}

# ---------------------------------------------------------------------------
# Preflight — only tools a bare Debian install does not guarantee.
# bash, coreutils, util-linux, findutils, grep, sed, and mawk are Debian
# Priority: required; missing them is not a supported state. Deliberately
# unchecked: lsblk/findmnt/mount/umount/mountpoint/blkid/readlink/stat/df.
# ---------------------------------------------------------------------------
preflight_tools() {
    local tool missing=0
    for tool in sudo file; do
        if ! cmd_exists "$tool"; then
            log_error "Required tool missing: $tool — install it (sudo apt install $tool)."
            missing=1
        fi
    done
    (( missing == 0 )) || return 1
    if ! cmd_exists eject; then
        log_warn "eject(1) not installed — ejection will fall back to udisksctl/power-off."
    fi
    if ! cmd_exists udisksctl; then
        log_warn "udisksctl not installed — ejection will fall back to eject(1)."
    fi
    if ! sudo -v; then
        log_error "sudo access required (run as the invoking user with sudo rights)."
        return 1
    fi
}

# preflight_ventoy — validate the repo-managed /opt/ventoy payload before
# any prompt. Upstream's Ventoy2Disk.sh needs boot/boot.img, sources
# tool/ventoy_lib.sh, and runs tool/VentoyWorker.sh; require them so a
# partial extraction cannot wipe the stick and then fail.
preflight_ventoy() {
    local bin="/usr/local/bin/Ventoy2Disk.sh" vdir f marker_ver
    if [[ ! -x "$bin" ]]; then
        log_error "Ventoy not deployed ($bin missing) — converge 20-packages first (install.py), then re-run."
        return 1
    fi
    vdir="$(dirname "$(readlink -f "$bin")")"
    if [[ -z "$vdir" || ! -d "$vdir" ]]; then
        log_error "Ventoy symlink $bin does not resolve to a directory — re-converge 20-packages."
        return 1
    fi
    for f in boot/boot.img tool/ventoy_lib.sh tool/VentoyWorker.sh ventoy/version; do
        if [[ ! -f "$vdir/$f" ]]; then
            log_error "Ventoy payload incomplete: $vdir/$f missing — re-converge 20-packages."
            return 1
        fi
    done
    if [[ ! -s "$VENTOY_MARKER" ]]; then
        log_error "Ventoy version marker missing or empty: $VENTOY_MARKER — re-converge 20-packages."
        return 1
    fi
    if ! marker_ver="$(<"$VENTOY_MARKER")"; then
        log_error "Cannot read Ventoy marker $VENTOY_MARKER — re-converge 20-packages."
        return 1
    fi
    if ! VENTOY_VER="$(<"$vdir/ventoy/version")"; then
        log_error "Cannot read $vdir/ventoy/version — re-converge 20-packages."
        return 1
    fi
    if [[ "$marker_ver" != "$VENTOY_VER" ]]; then
        log_error "Ventoy marker/payload mismatch ($marker_ver vs $VENTOY_VER) — re-converge 20-packages."
        return 1
    fi
    VENTOY_DIR="$vdir"
    log "Using Ventoy $VENTOY_VER from $VENTOY_DIR."
}

# preflight_files — the stick payload is copied from the checkout after the
# wipe; a missing source file must fail before any destructive step.
preflight_files() {
    local f
    for f in tools/bootstrap.sh docs/recovery/bootstrap-wifi.md \
             docs/recovery/new-machine.md tools/capture-network-state.sh; do
        if [[ ! -r "$REPO_ROOT/$f" ]]; then
            log_error "Repo file missing or unreadable: $REPO_ROOT/$f — run from a complete checkout."
            return 1
        fi
    done
}

pick_device() {
    local iso_path="${1:-}"
    local root_src root_disk
    root_src="$(findmnt -no SOURCE /)"
    root_disk="$(lsblk -dno PKNAME "$root_src" 2>/dev/null | tr -d '[:space:]')"
    [[ -z "$root_disk" ]] && root_disk="$(basename "$root_src")"
    root_disk="/dev/$root_disk"

    local iso_bytes=0 iso_name=""
    if [[ -n "$iso_path" ]]; then
        iso_bytes="$(stat -c %s "$iso_path" 2>/dev/null || printf '0')"
        [[ "$iso_bytes" =~ ^[0-9]+$ ]] || iso_bytes=0
        iso_name="$(basename "$iso_path")"
    fi

    log_step "Target USB stick (2/3) — all data on the chosen device will be wiped"

    # Candidates only: whole disks, never loop/ram/zram. Refusal rows are
    # shown but never numbered — no valid input can select them.
    local -a all_disks=() cand_devs=() cand_rows=() refuse_usb=() \
             refuse_media=() refuse_small=()
    local refuse_root="" d ddev dsize dbytes dmodel dcontent dinfo dtran
    readarray -t all_disks < <(lsblk -dn -o NAME,TYPE 2>/dev/null | awk '$2 == "disk" { print $1 }')
    for d in "${all_disks[@]}"; do
        case "$d" in loop*|ram*|zram*) continue ;; esac
        ddev="/dev/$d"
        dinfo="$(disk_transport "$ddev")"
        dtran="${dinfo#*|}"; dtran="${dtran%%|*}"
        log "scan: $ddev TRAN=${dtran:-unknown} SUBSYSTEMS=${dinfo##*|}"
        if [[ "$ddev" == "$root_disk" ]]; then
            refuse_root="$d"
            continue
        fi
        if [[ "$dtran" != "usb" && ":${dinfo##*|}" != *":usb:"* ]]; then
            refuse_usb+=("$d")
            continue
        fi
        if ! dev_media_loaded "$ddev"; then
            refuse_media+=("$d")
            continue
        fi
        dbytes="$(lsblk -bdn -o SIZE "$ddev" 2>/dev/null | tr -d '[:space:]')"
        [[ "$dbytes" =~ ^[0-9]+$ ]] || dbytes=0
        if ! has_iso_capacity "$dbytes" "$iso_bytes"; then
            refuse_small+=("$d")
            continue
        fi
        dsize="$(lsblk -dn -o SIZE "$ddev" 2>/dev/null | tr -d '[:space:]')"
        dmodel="$(lsblk -dn -o MODEL "$ddev" 2>/dev/null | sed 's/  */ /g')"
        dcontent="$(disk_content "$ddev")"
        cand_devs+=("$ddev")
        cand_rows+=("$ddev|$dsize|$dmodel|$dcontent")
    done
    if (( ${#cand_devs[@]} > 0 )); then
        echo "  #  DEVICE        SIZE    MODEL                CONTENT" >&2
        local i=1 row rdev rsize rmodel rcontent rest
        for row in "${cand_rows[@]}"; do
            rdev="${row%%|*}"; rest="${row#*|}"
            rsize="${rest%%|*}"; rest="${rest#*|}"
            rmodel="${rest%%|*}"; rcontent="${rest#*|}"
            printf '  %d) %-12s %-7s %-20.20s %s\n' "$i" "$rdev" "$rsize" "$rmodel" "$rcontent" >&2
            i=$((i + 1))
        done
    fi
    if [[ -n "$refuse_root" || "${#refuse_usb[@]}" -gt 0 \
          || "${#refuse_media[@]}" -gt 0 || "${#refuse_small[@]}" -gt 0 ]]; then
        echo "" >&2
        # Red = running system (danger/exclusion); yellow = wrong bus,
        # no media, or too small. Text tags carry the semantics when
        # color is off.
        [[ -n "$refuse_root" ]] && \
            printf '%s  /dev/%s  * RUNNING SYSTEM — not selectable%s\n' \
                "$C_RED" "$refuse_root" "$C_RESET" >&2
        local shown=0 u
        for u in "${refuse_usb[@]}"; do
            if (( shown < 2 )); then
                printf '%s  /dev/%s  * NON-USB — not selectable%s\n' \
                    "$C_YELLOW" "$u" "$C_RESET" >&2
                shown=$((shown + 1))
            fi
        done
        if (( ${#refuse_usb[@]} > shown )); then
            printf '  ... +%d more internal drive(s) hidden\n' \
                "$(( ${#refuse_usb[@]} - shown ))" >&2
        fi
        local shown_media=0 m
        for m in "${refuse_media[@]}"; do
            if (( shown_media < 2 )); then
                printf '%s  /dev/%s  * NO MEDIA (0 B) — reinsert or re-plug%s\n' \
                    "$C_YELLOW" "$m" "$C_RESET" >&2
                shown_media=$((shown_media + 1))
            fi
        done
        if (( ${#refuse_media[@]} > shown_media )); then
            printf '  ... +%d more no-media device(s) hidden\n' \
                "$(( ${#refuse_media[@]} - shown_media ))" >&2
        fi
        local shown_small=0 s
        for s in "${refuse_small[@]}"; do
            if (( shown_small < 2 )); then
                printf '%s  /dev/%s  * TOO SMALL for %s — not selectable%s\n' \
                    "$C_YELLOW" "$s" "${iso_name:-this ISO}" "$C_RESET" >&2
                shown_small=$((shown_small + 1))
            fi
        done
        if (( ${#refuse_small[@]} > shown_small )); then
            printf '  ... +%d more too-small device(s) hidden\n' \
                "$(( ${#refuse_small[@]} - shown_small ))" >&2
        fi
    fi
    if (( ${#cand_devs[@]} == 0 )); then
        echo "" >&2
        log_error "No selectable USB sticks found."
        return 1
    fi
    local choice dev
    read -rp "Stick number [1-${#cand_devs[@]}/q=quit]: " choice || choice=""
    if [[ -z "$choice" || "$choice" == [qQ] ]]; then
        log_error "Aborted."
        return 1
    fi
    if [[ "$choice" =~ ^[0-9]+$ ]] && (( choice >= 1 && choice <= ${#cand_devs[@]} )); then
        dev="${cand_devs[$((choice - 1))]}"
    else
        log_error "Pick a number 1-${#cand_devs[@]} — typed paths are not accepted."
        return 1
    fi
    if ! dev_media_loaded "$dev"; then
        log_error "$dev has no media (unplugged, ejected, or card removed) — reinsert and re-run."
        return 1
    fi
    # Mounted partitions: offer a verified unmount of THIS device's mounts
    # only (never anything else's), default abort. Proceeds solely on a
    # fresh post-umount re-check — a busy target still fails loud.
    local mounts
    # `|| true`: an all-empty unpruned list makes grep exit 1; under
    # `set -euo pipefail` that would silently kill the script.
    mounts="$(lsblk -rno MOUNTPOINT "$dev" 2>/dev/null | grep -v '^$' | tr '\n' ' ')" || true
    if [[ -n "${mounts// }" ]]; then
        local unmount_choice
        read -rp "Unmount$mounts and continue? [y/N]: " unmount_choice || unmount_choice=""
        case "$unmount_choice" in
            [yY]) ;;
            *) log_error "Aborted."; return 1 ;;
        esac
        local -a mps=() mp still
        readarray -t mps < <(lsblk -rno MOUNTPOINT "$dev" 2>/dev/null | grep -v '^$')
        for mp in "${mps[@]}"; do
            log_run sudo umount "$mp" || true
        done
        still="$(lsblk -rno MOUNTPOINT "$dev" 2>/dev/null | grep -v '^$' | tr '\n' ' ')" || true
        if [[ -n "${still// }" ]]; then
            log_error "Unmount failed ($still) — close files there (check: sudo fuser -mv <mountpoint>), then re-run."
            return 1
        fi
        log "Unmounted$mounts — re-checked clean."
    fi

    local dev_hl="${C_BOLD}${C_RED}${dev}${C_RESET}"
    echo "" >&2
    printf '  Device:   %s (%s %s, USB)\n' "$dev_hl" \
        "$(lsblk -dn -o SIZE "$dev" 2>/dev/null | tr -d '[:space:]')" \
        "$(lsblk -dn -o MODEL "$dev" 2>/dev/null | sed 's/  */ /g')" >&2
    echo "  Content:" >&2
    device_detail "$dev"
    if [[ -n "$iso_path" ]]; then
        printf '  ISO:      %s (%s, ISO 9660 OK)\n' "$(basename "$iso_path")" \
            "$(du -h "$iso_path" 2>/dev/null | cut -f1)" >&2
    fi
    local confirm
    read -rp "Wipe $dev and install Ventoy? [y/N]: " confirm || confirm=""
    if [[ "$confirm" != [yY] ]]; then
        log_error "Aborted."
        return 1
    fi
    printf '%s\n' "$dev"
}

main() {
    preflight_tools || return 1
    preflight_ventoy || return 1
    preflight_files || return 1

    local iso dev
    log_step "ISO image (1/3)"
    iso="$(pick_iso)" || return 1
    log "ISO: $iso"
    dev="$(pick_device "$iso")" || return 1

    # Explicit -s (Secure Boot) + -g (GPT): official docs and the in-tarball
    # README disagree on the Secure Boot default — never rely on it.
    # UEFI/GPT is our only target. -I = force reinstall over an existing
    # Ventoy, NOT non-interactive: VentoyWorker.sh always stops twice
    # (Continue? + Double-check). The y/N above is the only human decision;
    # answer both downstream stops programmatically. Exactly two lines —
    # never unbounded `yes |`: a future pin adding a third prompt reads
    # EOF → clean exit 0, and the blkid gate below fails loud.
    # Ventoy2Disk.sh requires CWD=its own dir (./boot/boot.img guard) but
    # its $0-based self-cd is symlink-blind (/usr/local/bin has no tool/),
    # so preflight_ventoy resolved the real dir; invoke from inside it
    # (subshell keeps cwd).
    log_step "Installing Ventoy (3/3)"
    echo "  ... writing image (full log: $LOG_FILE)" >&2
    if ! dev_media_loaded "$dev"; then
        log_error "$dev has no media (unplugged, ejected, or card removed) — reinsert and re-run."
        return 1
    fi
    if ! (cd "$VENTOY_DIR" && printf 'y\ny\n' | log_run sudo ./Ventoy2Disk.sh -I -s -g -L "$STICK_LABEL" "$dev"); then
        log_error "Ventoy install failed — full output in $LOG_FILE."
        return 1
    fi

    local stick_part blk_rc=0 part_parent chosen_disk
    # sudo: blkid lives in /usr/sbin (off normal user PATH) and needs
    # privilege to see fresh partitions — consistent with mkdir/mount/cp below.
    stick_part="$(sudo blkid -L "$STICK_LABEL")" || blk_rc=$?
    if (( blk_rc == 127 )); then
        log_error "blkid missing (util-linux) — cannot locate by-label/$STICK_LABEL."
        return 1
    fi
    if (( blk_rc != 0 )) || [[ -z "$stick_part" ]]; then
        log_error "by-label/$STICK_LABEL missing after install (blkid rc=$blk_rc) — upstream may have aborted; transcript: $LOG_FILE."
        return 1
    fi
    if [[ ! -b "$stick_part" ]]; then
        log_error "by-label/$STICK_LABEL resolved to $stick_part, which is not a block device — re-run."
        return 1
    fi
    chosen_disk="$(basename "$dev")"
    part_parent="$(lsblk -dno PKNAME "$stick_part" 2>/dev/null | tr -d '[:space:]')"
    if [[ "$part_parent" != "$chosen_disk" ]]; then
        log_error "by-label/$STICK_LABEL resolved to $stick_part (parent ${part_parent:-unknown}), not $dev — another Ventoy stick plugged?"
        return 1
    fi
    log_ok "Stick labeled, partition: $stick_part"

    # Explicit I/O gates (never bare under set -e): a yanked stick must
    # produce a named error + remedy, not a bare abort mid-listing.
    local io_fail="failed — stick unplugged or I/O error? Reinsert and re-run; -I reinstalls clean. Transcript: $LOG_FILE."
    sudo mkdir -p "$STICK_MOUNT"
    if ! mountpoint -q "$STICK_MOUNT"; then
        if ! sudo mount "$stick_part" "$STICK_MOUNT"; then
            log_error "Mount $stick_part $io_fail"
            return 1
        fi
    fi
    local iso_bytes avail_bytes
    iso_bytes="$(stat -c %s "$iso")" || { log_error "Cannot stat $iso — re-run."; return 1; }
    avail_bytes="$(df -B1 --output=avail "$STICK_MOUNT" 2>/dev/null | tail -n 1 | tr -d '[:space:]')"
    if [[ ! "$avail_bytes" =~ ^[0-9]+$ ]]; then
        log_error "Cannot determine free space on $STICK_MOUNT — aborting before copy."
        return 1
    fi
    if (( avail_bytes < iso_bytes + 5 * 1024 * 1024 )); then
        log_error "ISO needs $iso_bytes bytes, only $avail_bytes free on $STICK_MOUNT — use a larger stick or smaller ISO."
        return 1
    fi
    if ! sudo cp "$iso" "$STICK_MOUNT/"; then
        log_error "Copy $(basename "$iso") $io_fail"
        return 1
    fi
    if ! sudo cp "$REPO_ROOT/tools/bootstrap.sh" "$STICK_MOUNT/bootstrap.sh"; then
        log_error "Copy bootstrap.sh $io_fail"
        return 1
    fi
    if ! sudo cp "$REPO_ROOT/docs/recovery/bootstrap-wifi.md" "$STICK_MOUNT/bootstrap-wifi.md"; then
        log_error "Copy bootstrap-wifi.md $io_fail"
        return 1
    fi
    if ! sudo cp "$REPO_ROOT/docs/recovery/new-machine.md" "$STICK_MOUNT/new-machine.md"; then
        log_error "Copy new-machine.md $io_fail"
        return 1
    fi
    # TEMPORARY: one-shot network-state capture run by bootstrap.sh.
    if ! sudo cp "$REPO_ROOT/tools/capture-network-state.sh" "$STICK_MOUNT/capture-network-state.sh"; then
        log_error "Copy capture-network-state.sh $io_fail"
        return 1
    fi

    local missing=0
    for f in "$STICK_MOUNT/$(basename "$iso")" \
              "$STICK_MOUNT/bootstrap.sh" "$STICK_MOUNT/bootstrap-wifi.md" \
              "$STICK_MOUNT/new-machine.md" \
              "$STICK_MOUNT/capture-network-state.sh"; do
        if [[ -r "$f" ]]; then
            log_ok "present: $f"
        else
            log_error "missing: $f"
            missing=1
        fi
    done
    (( missing == 0 )) || return 1
    sync

    cat << 'EOF'
Stick ready. On each target PC, first boot shows the MokManager screen:
enroll ENROLL_THIS_KEY_IN_MOKMANAGER.cer once per machine, then reboot.
(Required again if Ventoy's Secure Boot CA ever changes — pinned Ventoy
keeps stick behavior stable until you deliberately bump the pin.)
EOF
    log_ok "Stick built — verifying on-stick files."

    # Post-build viewer (safe by construction: our mount, our files — a
    # plain ls, no mount syscall, no foreign filesystem parsers) followed
    # by the single end-of-run unmount point. Success path only: every
    # failure return above bypasses this offer, so a failed build still
    # leaves the stick mounted for forensics and never offers ejection.
    local stick_files
    if ! stick_files="$(ls -1A "$STICK_MOUNT" 2>/dev/null)"; then
        echo "" >&2
        log_warn "Cannot list $STICK_MOUNT (mount missing or I/O error) — skipping on-stick listing. Transcript: $LOG_FILE"
    else
        printf '  On the stick: %s\n' "$(printf '%s' "$stick_files" | tr '\n' ' ')" >&2
    fi
    local eject_choice
    read -rp "Unmount and eject $dev so it can be removed now? [y/N]: " eject_choice || eject_choice=""
    case "$eject_choice" in
        [yY]) ;;
        *)
            echo "" >&2
            log_ok "Done — build complete; stick left mounted at $STICK_MOUNT."
            printf '  Unmount before removing: sudo umount %s\n' "$STICK_MOUNT" >&2
            return 0
            ;;
    esac
    if ! dev_present "$dev"; then
        # Already gone (yanked) — the eject outcome is achieved; warn,
        # don't fake an OK and don't noise-error.
        echo "" >&2
        log_warn "Done — build complete; $dev already gone."
        return 0
    fi
    if mountpoint -q "$STICK_MOUNT"; then
        if ! log_run sudo umount "$STICK_MOUNT"; then
            log_error "Unmount of $STICK_MOUNT failed — close files there, then: sudo umount $STICK_MOUNT."
            return 1
        fi
        local eject_still
        eject_still="$(lsblk -rno MOUNTPOINT "$dev" 2>/dev/null | grep -v '^$' | tr '\n' ' ')" || true
        if [[ -n "${eject_still// }" ]]; then
            log_error "Build complete, but $dev is still mounted ($eject_still) — do not remove yet."
            return 1
        fi
    fi
    # udisksctl first: user-session, no sudo, spins down + cuts power
    # (true "safe to remove"); eject(1) as fallback (proven on bridges
    # that ignore the power-off request). Removal is asynchronous, so
    # always poll the node instead of trusting a command's rc alone.
    local gone=0 i po_rc=0 ej_rc=0
    if cmd_exists udisksctl; then
        log_run udisksctl power-off -b "$dev" || po_rc=$?
        for i in {1..20}; do
            if dev_ejected "$dev"; then gone=1; break; fi
            sleep 0.25
        done
        (( gone )) || log "udisksctl power-off rc=$po_rc left $dev present — falling back to eject(1)."
    else
        log "udisksctl not installed — trying eject(1)."
    fi
    if (( gone == 0 )); then
        if cmd_exists eject; then
            log_run sudo eject "$dev" || ej_rc=$?
            for i in {1..20}; do
                if dev_ejected "$dev"; then gone=1; break; fi
                sleep 0.25
            done
            (( gone )) || log "eject rc=$ej_rc left $dev present."
        else
            log "eject(1) not installed."
        fi
    fi
    echo "" >&2
    if (( gone )); then
        log_ok "Done — build complete; $dev ejected. Safe to remove now."
        return 0
    fi
    if ! cmd_exists udisksctl && ! cmd_exists eject; then
        log_warn "Done — build complete; $dev unmounted and synced (neither udisksctl nor eject installed) — unplug when idle."
    else
        log_warn "Done — build complete; $dev unmounted and synced but still powered. Unplug when idle."
    fi
    return 0
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    main "$@"
fi
