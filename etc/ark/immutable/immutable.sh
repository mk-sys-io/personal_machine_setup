#!/usr/bin/env bash
# shellcheck disable=SC1083
# (SC1083: literal braces in the gomplate markers below are intentional.)
# immutable.sh — whitelisted chattr +i wrapper (sudoers-gated operator access).
#
# Replaces the blanket `NOPASSWD: /usr/bin/chattr` sudoers rule. Every flag
# mutation the operator may perform is routed here; paths outside the
# whitelist are refused before any chattr call runs. All flag handling still
# lives in immutable_lib.py (verify-after-set, crash-safe repair, exit codes)
# — this wrapper is a path gate, not a second implementation.
#
# Ops (mirror immutable_lib.py): set | clear | repair | is
#   set/clear/is: exactly one whitelisted path
#   repair:       one or more whitelisted paths
#
# Root-only (invoked via sudoers NOPASSWD). Deployed to
# {{ .Env.ARK_DATA_PATH }}/scripts/immutable.sh.

set -u

ARK_LIB={{ .Env.ARK_DATA_PATH }}/scripts/immutable_lib.py

# Whitelisted paths — the only files the operator may touch flags on.
# metadata.json is tiered down (no +i at rest) but stays whitelisted so a
# stale flag from a pre-tier-down deploy can still be cleared.
WHITELIST=(
    {{ .Env.ARK_DATA_PATH }}/mode
    {{ .Env.ARK_DATA_PATH }}/cask/system.cask
    {{ .Env.ARK_DATA_PATH }}/cask/mobile.cask
    {{ .Env.ARK_DATA_PATH }}/cask/metadata.json
    /etc/resolv.conf
)

usage() {
    echo "Usage: immutable.sh set|clear|repair|is PATH..." >&2
    echo "  set/clear/is: exactly one whitelisted path" >&2
    echo "  repair:       one or more whitelisted paths" >&2
    exit 2
}

whitelisted() {
    local path="$1"
    local w
    for w in "${WHITELIST[@]}"; do
        if [[ "$path" == "$w" ]]; then
            return 0
        fi
    done
    return 1
}

[[ $# -ge 2 ]] || usage

op="$1"
case "$op" in
    set|clear|is)
        [[ $# -eq 2 ]] || usage
        paths=("$2")
        ;;
    repair)
        shift
        paths=("$@")
        ;;
    *)
        usage
        ;;
esac

for p in "${paths[@]}"; do
    if ! whitelisted "$p"; then
        echo "ERROR: refused — $p is not a whitelisted immutable path" >&2
        exit 1
    fi
done

exec python3 "$ARK_LIB" "$op" "${paths[@]}"
