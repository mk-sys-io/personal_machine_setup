#!/usr/bin/env bash
set -euo pipefail

# ---------------------------------------------------------------------------
# gopass.sh — Canonical secret reader (Phase 12-B)
#
# Usage: gopass.sh <entry> <field>
#   Prints the field value on stdout. Errors (cause + fix hint) go to
#   stderr; exit codes mirror the gopass FM table in docs/gopass.md.
#
# Contract-canonical, not code-canonical. The FM table lives in
# docs/gopass.md; this file is the executable CLI every runtime calls
# directly (bash: $(...), Python/TS later: subprocess/execFile) — no
# sourcing, no cross-language imports. tools/provider_registry/config.py
# is an independent Python accessor for provider_registry/pi_setup, not
# this script's caller. No other-runtime shims exist (YAGNI); add none
# until a real consumer appears.
#
# Consumer contract: capture into a non-exported local, unset after use,
# prefer pipes / `gopass env --stdin`, never log the secret.
# Required-field semantics: a missing entry/field (gopass exit 10) fails
# loud with the insert command (unlike config.py, which returns None).
# ---------------------------------------------------------------------------

if [[ $# -ne 2 ]]; then
    echo "ERROR: usage: gopass.sh <entry> <field>" >&2
    exit 2
fi

entry="$1"
field="$2"

if ! command -v gopass >/dev/null 2>&1; then
    echo "ERROR: gopass not found — run install.sh (lib/20-packages.sh installs it)" >&2
    exit 127
fi

err_file=$(mktemp)
trap 'rm -f "$err_file"' EXIT

if output=$(gopass show "$entry" "$field" 2>"$err_file"); then
    if [[ -z "$output" ]]; then
        echo "ERROR: gopass entry '$entry' field '$field' is empty — run: gopass insert $entry $field" >&2
        exit 10
    fi
    printf '%s' "$output"
    exit 0
else
    rc=$?
    err=$(cat "$err_file" 2>/dev/null || true)
    # NOTE: installed gopass 1.17.0 reports a missing entry as exit 11
    # ("entry is not in the password store"), not the exit 10 older
    # versions used. Both stay hard errors here; the required-field
    # insert hint is keyed on exit 10 per the docs/gopass.md FM table,
    # and gopass's own stderr (shown below) carries the missing-entry
    # cause for any other code.
    case "$rc" in
        10)
            echo "ERROR: gopass entry '$entry' field '$field' not found — run: gopass insert $entry $field" >&2
            ;;
        6)
            echo "ERROR: gopass store not initialized — run: gopass setup" >&2
            ;;
        *)
            if [[ -n "$err" ]]; then
                echo "ERROR: gopass show $entry $field failed (exit $rc): $err" >&2
            else
                echo "ERROR: gopass show $entry $field failed (exit $rc)" >&2
            fi
            ;;
    esac
    exit "$rc"
fi
