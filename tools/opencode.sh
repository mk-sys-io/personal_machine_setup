#!/usr/bin/env bash
# Wrapper for opencode — applies internet network namespace when needed.
# gtr runs commands directly (not through a shell), so aliases don't expand.
# This wrapper makes opencode available as a PATH command for gtr and manual use.
#
# The namespace is required in locked mode because nftables blacklists internet
# access. In unrestricted/focused modes, internet is accessible directly.

OPENCODE_BIN="$HOME/.opencode/bin/opencode"

# If namespace is running — use it (always works)
if systemctl is-active --quiet internet-netns 2>/dev/null; then
    # enter-internet-netns has NOPASSWD sudoers grant — no password prompt
    exec sudo enter-internet-netns "$OPENCODE_BIN" "$@"
fi

# Namespace not running — check ark mode
MODE=$(cat /opt/ark/mode 2>/dev/null || echo "unrestricted")

if [ "$MODE" != "locked" ]; then
    # Unrestricted or focused — internet works directly, no prompt needed
    exec "$OPENCODE_BIN" "$@"
fi

# Locked mode — namespace is required
if [ -t 0 ]; then
    # Interactive — prompt
    echo "Warning: internet-netns service is not running (locked mode)" >&2
    echo "opencode will run without network namespace (internet access blocked)" >&2
    read -r -p "Continue without namespace? [y/N]: " confirm
    if [[ "$confirm" =~ ^[Yy]$ ]]; then
        exec "$OPENCODE_BIN" "$@"
    fi
else
    # Non-interactive (gtr, scripts) — abort with clear error
    echo "Error: internet-netns service is not running (locked mode)" >&2
    echo "Start the namespace: sudo systemctl start internet-netns" >&2
    exit 1
fi

echo "Aborted." >&2
exit 1
