#!/usr/bin/env bash
# shellcheck shell=bash
# Skeleton. Copy, rename, delete unused parts. Cite --help, not this header.
set -euo pipefail

EXIT_OK=0
EXIT_FAIL=1
EXIT_USAGE=2

RERUN="$(printf '%q ' "$@")"
RERUN=${RERUN% }
JSON=0
ASSENT=0
DRY_RUN=0
cmd=
args=()

usage() {
    cat <<'EOF'
usage: tool [OPTIONS] <command>

commands:
  show    read state
  apply   change state (mutating)

options:
  --json          machine-readable output (no prompts)
  -y, --yes       skip confirmation
  --dry-run       print the plan, write nothing
  -h, --help      show this help
EOF
}

die() { printf 'error: %s\n' "$*" >&2; exit "$EXIT_FAIL"; }

confirm() {
    local answer
    [[ -t 0 ]] || return 1
    read -r -p "$1 [y/N] " answer || return 1
    case "${answer,,}" in y|yes) return 0 ;; *) return 1 ;; esac
}

require_consent() {
    local question=$1 rc=0
    # YES is a CI convenience, not the documented path (that is --yes).
    if [[ $ASSENT -eq 1 || ${YES:-} == 1 ]]; then return 0; fi
    if [[ $JSON -eq 1 || ! -t 0 ]]; then
        printf 'error: no prompts here; re-run: tool %s --yes\n' "$RERUN" >&2
        return "$EXIT_USAGE"
    fi
    confirm "$question" || rc=$?
    [[ $rc -eq 0 ]] || die "aborted"
    return 0
}

emit_json() { printf '%s\n' "$1"; }

cleanup() { :; }
trap 'exit 130' INT TERM
trap cleanup EXIT

while [ $# -gt 0 ]; do
    case $1 in
        --json) JSON=1 ;;
        -y|--yes) ASSENT=1 ;;
        --dry-run) DRY_RUN=1 ;;
        -h|--help) usage; exit "$EXIT_OK" ;;
        --) shift; break ;;
        -*) printf 'error: unknown flag: %s\n' "$1" >&2; usage >&2; exit "$EXIT_USAGE" ;;
        *)
            if [[ -z $cmd ]]; then cmd=$1; else args+=("$1"); fi
            ;;
    esac
    shift
done
args+=("$@")

[[ -n $cmd ]] || { usage >&2; exit "$EXIT_USAGE"; }

case $cmd in
    show)
        if [[ $JSON -eq 1 ]]; then emit_json '{"ok":true}'; else printf 'ok\n'; fi
        ;;
    apply)
        if [[ $DRY_RUN -eq 1 ]]; then printf 'would apply\n'; exit "$EXIT_OK"; fi
        rc=0
        require_consent "Apply?" || rc=$?
        [[ $rc -eq 0 ]] || exit "$rc"
        ;;
    *) printf 'error: unknown command: %s\n' "$cmd" >&2; usage >&2; exit "$EXIT_USAGE" ;;
esac
