#!/usr/bin/env bash
# ventoy.sh — canonical "Ventoy is deployed and functional" check.
#
# Executed by path, never sourced. Pure value producer: no common.sh, no shell
# options, no env mutation, no transcripts. Both consumers exec this same file:
#   - tools/make-ventoy-stick.sh  (preflight_ventoy)
#   - lib/20-packages.py          (ventoy_probe)
#
# Usage: ventoy.sh [root=/opt/ventoy] [expected-version]
#
# stdout: exactly one line  <resolved-dir>|<marker-version>
# stderr: zero or more      ventoy: <reason>
# rc:     0 functional, 1 not

_ventoy_fail() {
    printf 'ventoy: %s\n' "$*" >&2
    return 1
}

ventoy_check() {
    local root="${1:-/opt/ventoy}" expected="${2:-}"
    local bin="/usr/local/bin/Ventoy2Disk.sh"
    local real dir root_real marker payload_ver marker_ver f

    [[ "$root" == /* ]] || { _ventoy_fail "root must be absolute: $root"; return 1; }
    [[ -d "$root" ]] || { _ventoy_fail "root is not a directory: $root"; return 1; }

    [[ -f "$bin" ]] || { _ventoy_fail "missing $bin"; return 1; }
    [[ -x "$bin" ]] || { _ventoy_fail "$bin is not executable"; return 1; }
    real="$(readlink -f -- "$bin")" || { _ventoy_fail "cannot resolve $bin"; return 1; }
    [[ -n "$real" && -f "$real" ]] || { _ventoy_fail "$bin does not resolve to a file"; return 1; }
    dir="$(dirname -- "$real")"
    [[ -d "$dir" ]] || { _ventoy_fail "resolved directory missing: $dir"; return 1; }

    root_real="$(readlink -f -- "$root")" || { _ventoy_fail "cannot resolve $root"; return 1; }
    [[ "$dir" == "$root_real" ]] || {
        _ventoy_fail "$bin resolves to $dir, outside $root_real"
        return 1
    }

    for f in boot/boot.img tool/ventoy_lib.sh tool/VentoyWorker.sh ventoy/version; do
        [[ -f "$dir/$f" ]] || { _ventoy_fail "payload incomplete: $dir/$f missing"; return 1; }
    done
    [[ -s "$dir/boot/boot.img" ]] || {
        _ventoy_fail "payload incomplete: $dir/boot/boot.img is empty"
        return 1
    }

    marker="$root_real/.installed-version"
    [[ -f "$marker" ]] || { _ventoy_fail "version marker missing: $marker"; return 1; }
    [[ -s "$marker" ]] || { _ventoy_fail "version marker empty: $marker"; return 1; }
    [[ -r "$marker" ]] || { _ventoy_fail "version marker unreadable: $marker"; return 1; }
    marker_ver="$(<"$marker")" || { _ventoy_fail "cannot read version marker: $marker"; return 1; }
    [[ -n "$marker_ver" ]] || { _ventoy_fail "version marker empty: $marker"; return 1; }

    [[ -r "$dir/ventoy/version" ]] || { _ventoy_fail "cannot read $dir/ventoy/version"; return 1; }
    payload_ver="$(<"$dir/ventoy/version")" || { _ventoy_fail "cannot read $dir/ventoy/version"; return 1; }
    [[ -n "$payload_ver" ]] || { _ventoy_fail "$dir/ventoy/version is empty"; return 1; }

    [[ "$marker_ver" == "$payload_ver" ]] || {
        _ventoy_fail "marker/payload mismatch ($marker_ver vs $payload_ver)"
        return 1
    }
    if [[ -n "$expected" ]]; then
        [[ "$marker_ver" == "$expected" ]] || {
            _ventoy_fail "deployed version $marker_ver != expected $expected"
            return 1
        }
    fi

    printf '%s|%s\n' "$dir" "$marker_ver"
    return 0
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    ventoy_check "$@"
fi
