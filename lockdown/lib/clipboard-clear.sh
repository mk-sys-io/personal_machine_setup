#!/bin/bash
# Adapter: clear clipboard — swap copyq/wl-clipboard here, not in seal
for tool in copyq wl-copy; do
    if command -v "$tool" &>/dev/null; then
        case "$tool" in
            copyq)   copyq clear ;;
            wl-copy) wl-copy --clear ;;
        esac
        exit 0
    fi
done
echo "clipboard-clear: no supported clipboard tool found" >&2
exit 1
