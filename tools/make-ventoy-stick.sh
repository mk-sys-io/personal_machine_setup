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
DEB_CD="https://cdimage.debian.org/debian-cd/current/amd64/iso-cd"

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

download_netinst() {
    local sums="" iso_name one_sha=""
    local dl_timeout="${CURL_TIMEOUT_DOWNLOAD:-240}"
    if [[ ! "$dl_timeout" =~ ^[0-9]+$ ]]; then
        log_error "CURL_TIMEOUT_DOWNLOAD='$dl_timeout' is not numeric."
        return 1
    fi
    sums="$(mktemp)" || return 1
    log "Fetching SHA512SUMS from $DEB_CD ..."
    curl -fsSL --retry 3 --retry-delay 5 --max-time 60 -o "$sums" "$DEB_CD/SHA512SUMS"
    iso_name="$(grep -oE 'debian-[0-9][^[:space:]]*-amd64-netinst\.iso' "$sums" | head -1)"
    if [[ -z "$iso_name" ]]; then
        log_error "No netinst image listed in upstream SHA512SUMS."
        rm -f "$sums"
        return 1
    fi
    mkdir -p "$HOME/ISOs"
    log "Downloading $iso_name ..."
    curl -fsSL --retry 3 --retry-delay 5 --max-time "$dl_timeout" \
        -o "$HOME/ISOs/$iso_name" "$DEB_CD/$iso_name"
    one_sha="$(mktemp)" || { rm -f "$sums"; return 1; }
    # Armed late so both paths are covered (RETURN trap is function-scoped).
    # Double-quoted: paths expand now (fixed for this call) — intended.
    # shellcheck disable=SC2064
    trap "rm -f '$sums' '$one_sha' 2>/dev/null || true" RETURN
    grep " $iso_name\$" "$sums" > "$one_sha"
    (cd "$HOME/ISOs" && sha512sum -c "$one_sha")
    printf '%s\n' "$HOME/ISOs/$iso_name"
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
    echo "Options: <number> | p = another path | d = download fresh netinst | s = scan home | q = quit" >&2
    read -rp "ISO choice: " choice
    case "$choice" in
        q|Q) return 1 ;;
        d|D) iso="$(download_netinst)" || return 1 ;;
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
        [[ "$line" =~ NAME=\"([^\"]*)\" ]] && name="${BASH_REMATCH[1]}" || continue
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
        [[ "$line" =~ NAME=\"([^\"]*)\" ]] && name="${BASH_REMATCH[1]}" || continue
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

pick_device() {
    local iso_path="${1:-}"
    local root_src root_disk
    root_src="$(findmnt -no SOURCE /)"
    root_disk="$(lsblk -dno PKNAME "$root_src" 2>/dev/null | tr -d '[:space:]')"
    [[ -z "$root_disk" ]] && root_disk="$(basename "$root_src")"
    root_disk="/dev/$root_disk"

    log_step "Target USB stick (2/3) — all data on the chosen device will be wiped"

    # Candidates only: whole disks, never loop/ram/zram. Refusal rows are
    # shown but never numbered — no valid input can select them.
    local -a all_disks=() cand_devs=() cand_rows=() refuse_usb=()
    local refuse_root="" d ddev dsize dmodel dcontent dinfo dtran
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
        dsize="$(lsblk -dn -o SIZE "$ddev" 2>/dev/null | tr -d '[:space:]')"
        dmodel="$(lsblk -dn -o MODEL "$ddev" 2>/dev/null | sed 's/  */ /g')"
        dcontent="$(disk_content "$ddev")"
        cand_devs+=("$ddev")
        cand_rows+=("$ddev|$dsize|$dmodel|$dcontent")
    done
    if (( ${#cand_devs[@]} == 0 )); then
        log_error "No selectable USB sticks found."
        return 1
    fi
    echo "  #  DEVICE        SIZE    MODEL                CONTENT" >&2
    local i=1 row rdev rsize rmodel rcontent rest
    for row in "${cand_rows[@]}"; do
        rdev="${row%%|*}"; rest="${row#*|}"
        rsize="${rest%%|*}"; rest="${rest#*|}"
        rmodel="${rest%%|*}"; rcontent="${rest#*|}"
        printf '  %d) %-12s %-7s %-20.20s %s\n' "$i" "$rdev" "$rsize" "$rmodel" "$rcontent" >&2
        i=$((i + 1))
    done
    if [[ -n "$refuse_root" || "${#refuse_usb[@]}" -gt 0 ]]; then
        echo "" >&2
        # Red = running system (danger/exclusion); yellow = wrong bus
        # (ignored). Text tags carry the semantics when color is off.
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
    if ! dev_present "$dev"; then
        log_error "$dev vanished (unplugged?) — reinsert and re-run."
        return 1
    fi
    # Mounted partitions: offer a verified unmount of THIS device's mounts
    # only (never anything else's), default abort. Proceeds solely on a
    # fresh post-umount re-check — a busy target still fails loud.
    local mounts
    mounts="$(lsblk -rno MOUNTPOINT "$dev" 2>/dev/null | grep -v '^$' | tr '\n' ' ')"
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
        still="$(lsblk -rno MOUNTPOINT "$dev" 2>/dev/null | grep -v '^$' | tr '\n' ' ')"
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
    if [[ ! -x /usr/local/bin/Ventoy2Disk.sh || ! -f "$VENTOY_MARKER" ]]; then
        log_error "Ventoy not deployed — converge 20-packages first (install.py), then re-run."
        return 1
    fi
    log "Using Ventoy $(cat "$VENTOY_MARKER") from /opt/ventoy."

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
    # so resolve the real dir and invoke from inside it (subshell keeps cwd).
    local vdir
    vdir="$(dirname "$(readlink -f /usr/local/bin/Ventoy2Disk.sh)")"
    if [[ -z "$vdir" || ! -f "$vdir/boot/boot.img" ]]; then
        log_error "Ventoy payload missing under ${vdir:-<unresolved>} — re-converge 20-packages."
        return 1
    fi
    log_step "Installing Ventoy (3/3)"
    echo "  ... writing image (full log: $LOG_FILE)" >&2
    if ! dev_present "$dev"; then
        log_error "$dev vanished (unplugged?) — reinsert and re-run."
        return 1
    fi
    if ! (cd "$vdir" && printf 'y\ny\n' | log_run sudo ./Ventoy2Disk.sh -I -s -g -L "$STICK_LABEL" "$dev"); then
        log_error "Ventoy install failed — full output in $LOG_FILE."
        return 1
    fi

    local stick_part blk_rc=0
    # sudo: blkid lives in /usr/sbin (off normal user PATH) and needs
    # privilege to see fresh partitions — consistent with mkdir/mount/cp below.
    stick_part="$(sudo blkid -L "$STICK_LABEL")" || blk_rc=$?
    if (( blk_rc != 0 )) || [[ -z "$stick_part" ]]; then
        log_error "by-label/$STICK_LABEL missing after install (blkid rc=$blk_rc) — upstream may have aborted; transcript: $LOG_FILE."
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

    local missing=0
    for f in "$STICK_MOUNT/$(basename "$iso")" \
              "$STICK_MOUNT/bootstrap.sh" "$STICK_MOUNT/bootstrap-wifi.md" \
              "$STICK_MOUNT/new-machine.md"; do
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
    log_ok "Done — safe to unmount $STICK_MOUNT and remove the stick."

    # Post-build viewer (safe by construction: our mount, our files — a
    # plain ls, no mount syscall, no foreign filesystem parsers) followed
    # by the single end-of-run unmount point. Success path only: every
    # failure return above bypasses this offer, so a failed build still
    # leaves the stick mounted for forensics and never offers ejection.
    local stick_files
    if ! stick_files="$(ls -1A "$STICK_MOUNT" 2>/dev/null)"; then
        log_error "Cannot list $STICK_MOUNT — stick unplugged or I/O error? Transcript: $LOG_FILE."
        return 1
    fi
    printf '  On the stick: %s\n' "$(printf '%s' "$stick_files" | tr '\n' ' ')" >&2
    local eject_choice
    read -rp "Unmount and eject $dev so it can be removed now? [y/N]: " eject_choice || eject_choice=""
    case "$eject_choice" in
        [yY]) ;;
        *) return 0 ;;
    esac
    if ! dev_present "$dev"; then
        # Already gone (yanked) — the eject outcome is achieved; warn,
        # don't fake an OK and don't noise-error.
        log_warn "Device already gone — nothing to eject."
        return 0
    fi
    if mountpoint -q "$STICK_MOUNT"; then
        if ! log_run sudo umount "$STICK_MOUNT"; then
            log_error "Unmount of $STICK_MOUNT failed — close files there, then: sudo umount $STICK_MOUNT."
            return 1
        fi
        local eject_still
        eject_still="$(lsblk -rno MOUNTPOINT "$dev" 2>/dev/null | grep -v '^$' | tr '\n' ' ')"
        if [[ -n "${eject_still// }" ]]; then
            log_error "Still mounted ($eject_still) — close files there (check: sudo fuser -mv <mountpoint>)."
            return 1
        fi
    fi
    # udisksctl first: user-session, no sudo, spins down + cuts power
    # (true "safe to remove"); eject(1) as fallback; honest failure last.
    if command -v udisksctl >/dev/null 2>&1 && udisksctl power-off -b "$dev" 2>/dev/null; then
        log_ok "Stick ejected — safe to remove."
    elif sudo eject "$dev" 2>/dev/null; then
        log_ok "Stick ejected — safe to remove."
    else
        log_error "Eject failed — remove manually when idle: sudo eject $dev."
        return 1
    fi
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    main "$@"
fi
