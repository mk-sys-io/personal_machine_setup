#!/usr/bin/env bash
# tools/make-ventoy-stick.sh — build the Debian install stick (builder-side).
#
# Implements the six manual-layout steps from docs/bootstrap-wifi.md
# ("Stick-builder fallback") one-for-one, so script and doc cannot drift:
# wipe-confirm /dev/sdX → Ventoy2Disk.sh → verify label → ISO →
# copy stick-root files → verify listing.
#
# Runs on the BUILDER (live iron with /opt/ventoy from the 20-packages
# converge); never on blank iron. DESTRUCTIVE to the target USB: all
# data on it is wiped after an explicit type-YES confirm.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=../lib/common.sh
source "$REPO_ROOT/lib/common.sh"

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
    local sums iso_name one_sha
    sums="$(mktemp)"
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
    curl -fsSL --retry 3 --retry-delay 5 --max-time "$CURL_TIMEOUT_DOWNLOAD" \
        -o "$HOME/ISOs/$iso_name" "$DEB_CD/$iso_name"
    one_sha="$(mktemp)"
    grep " $iso_name\$" "$sums" > "$one_sha"
    (cd "$HOME/ISOs" && sha512sum -c "$one_sha")
    rm -f "$sums" "$one_sha"
    printf '%s\n' "$HOME/ISOs/$iso_name"
}

scan_home_isos() {
    log "Scanning home (pruned: .cache, go, node_modules, .git) — may take ~1 min ..."
    find "$HOME" \
        \( -path "$HOME/.cache/*" -o -path "$HOME/go/*" \
           -o -path "*/node_modules/*" -o -path "*/.git/*" \) -prune \
        -o -type f -name '*.iso' -print 2>/dev/null
}

# Prints the chosen ISO path. Returns 1 on quit/invalid.
pick_iso() {
    local candidates=() choice iso
    mapfile -t candidates < <(tier1_isos)
    if (( ${#candidates[@]} > 0 )); then
        echo "Found ISOs (newest first):"
        local i=1 c mark
        for c in "${candidates[@]}"; do
            mark=" "
            [[ "$(basename "$c")" == *netinst* ]] && mark="*"
            printf '  %d)%s %s\n' "$i" "$mark" "$c"
            (( i++ ))
        done
        echo "  (* = netinst match)"
    else
        echo "No ISOs in ~/ISOs or ~/Downloads."
    fi
    echo "Options: <number> | p = another path | d = download fresh netinst | s = scan home | q = quit"
    read -rp "ISO choice: " choice
    case "$choice" in
        q|Q) return 1 ;;
        d|D) iso="$(download_netinst)" || return 1 ;;
        p|P)
            read -rp "ISO path: " iso
            ;;
        s|S)
            mapfile -t candidates < <(scan_home_isos)
            if (( ${#candidates[@]} == 0 )); then
                log_error "Home scan found nothing."
                return 1
            fi
            local j=1
            for c in "${candidates[@]}"; do
                printf '  %d) %s\n' "$j" "$c"
                (( j++ ))
            done
            read -rp "ISO number: " choice
            iso="${candidates[$(( choice - 1 ))]:-}"
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
pick_device() {
    echo "Block devices:"
    lsblk -d -o NAME,SIZE,MODEL,TRAN 2>/dev/null || lsblk -d -o NAME,SIZE,MODEL
    local dev input
    read -rp "Target USB device (e.g. /dev/sdX): " input
    dev="${input#/dev/}"
    dev="/dev/$dev"
    if [[ ! -b "$dev" ]]; then
        log_error "Not a block device: $dev."
        return 1
    fi
    local root_src root_disk
    root_src="$(findmnt -no SOURCE /)"
    root_disk="/dev/$(lsblk -no PKNAME "$root_src" 2>/dev/null || basename "$root_src")"
    if [[ "$dev" == "$root_disk" ]]; then
        log_error "Refusing: $dev holds the running system ($root_src)."
        return 1
    fi
    if [[ -n "$(lsblk -no MOUNTPOINT "$dev" 2>/dev/null | tr -d '[:space:]')" ]]; then
        log_error "$dev (or a partition) is mounted — unmount first."
        return 1
    fi
    echo "Size: $(lsblk -dno SIZE "$dev" 2>/dev/null) — informational only, no size gate."
    local confirm
    echo "ALL DATA on $dev will be WIPED."
    read -rp "Type YES to confirm: " confirm
    [[ "$confirm" == "YES" ]] || { log_error "Aborted."; return 1; }
    printf '%s\n' "$dev"
}

main() {
    if [[ ! -x /usr/local/bin/Ventoy2Disk.sh || ! -f "$VENTOY_MARKER" ]]; then
        log_error "Ventoy not deployed — converge 20-packages first (install.sh), then re-run."
        return 1
    fi
    log "Using Ventoy $(cat "$VENTOY_MARKER") from /opt/ventoy."

    local iso dev
    iso="$(pick_iso)" || return 1
    log "ISO: $iso"
    dev="$(pick_device)" || return 1

    # Explicit -s (Secure Boot) + -g (GPT): official docs and the in-tarball
    # README disagree on the Secure Boot default — never rely on it.
    # UEFI/GPT is our only target. -I (force) is honest here: the user
    # already typed YES to the wipe above.
    sudo /usr/local/bin/Ventoy2Disk.sh -I -s -g -L "$STICK_LABEL" "$dev"

    local stick_part
    stick_part="$(blkid -L "$STICK_LABEL" || true)"
    if [[ -z "$stick_part" ]]; then
        log_error "by-label/$STICK_LABEL missing after install."
        return 1
    fi
    log_ok "Stick labeled, partition: $stick_part"

    sudo mkdir -p "$STICK_MOUNT"
    if ! mountpoint -q "$STICK_MOUNT"; then
        sudo mount "$stick_part" "$STICK_MOUNT"
    fi
    sudo cp "$iso" "$STICK_MOUNT/"
    sudo cp "$REPO_ROOT/tools/bootstrap.sh" "$STICK_MOUNT/bootstrap.sh"
    sudo cp "$REPO_ROOT/docs/bootstrap-wifi.md" "$STICK_MOUNT/bootstrap-wifi.md"

    local missing=0
    for f in "$STICK_MOUNT/$(basename "$iso")" \
             "$STICK_MOUNT/bootstrap.sh" "$STICK_MOUNT/bootstrap-wifi.md"; do
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
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    main "$@"
fi
