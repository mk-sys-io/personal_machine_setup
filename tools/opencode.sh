#!/usr/bin/env bash
# Wrapper for opencode — applies internet network namespace when needed.
# gtr runs commands directly (not through a shell), so aliases don't expand.
# This wrapper makes opencode available as a PATH command for gtr and manual use.
#
# The mode/service/prompt context decision now lives in `netmgr namespace run`
# (netmgr/namespace.py). This wrapper is a thin shim through its `inet` entry
# point (deployed by 60-ark.sh at P14; until then it is inert).

OPENCODE_BIN="$HOME/.opencode/bin/opencode"

exec inet "$OPENCODE_BIN" "$@"
