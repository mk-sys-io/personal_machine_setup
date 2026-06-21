#!/bin/bash
set -euo pipefail

SEAL_DIR="$HOME/.config/seal"

if [ ! -f "$SEAL_DIR/sealed-credentials" ]; then
    echo "ERROR: No sealed credentials at $SEAL_DIR/sealed-credentials" >&2
    exit 1
fi

mkdir -p "$SEAL_DIR"
echo "[$(date -u '+%Y-%m-%d %H:%M:%S UTC')] unseal: decrypting..." >> "$SEAL_DIR/seal.log"

if /usr/local/bin/tle -d \
    -o "$SEAL_DIR/recovery-credentials" \
    "$SEAL_DIR/sealed-credentials" \
    2>> "$SEAL_DIR/seal.log"; then
    echo "[$(date -u '+%Y-%m-%d %H:%M:%S UTC')] unseal: SUCCESS" >> "$SEAL_DIR/seal.log"
    echo ""
    echo "  [OK] Recovery credentials decrypted to $SEAL_DIR/recovery-credentials"
    echo ""
    echo "Contents of $SEAL_DIR/recovery-credentials:"
    echo "----------------------------------------"
    cat "$SEAL_DIR/recovery-credentials"
    echo "----------------------------------------"
    echo ""

    ROOT_PW=$(grep '^root_password=' "$SEAL_DIR/recovery-credentials" | cut -d= -f2- | tail -1 || true)

    if [ -n "$ROOT_PW" ]; then
        COPIED_WITH=""
        if echo "$ROOT_PW" | copyq copy 2>/dev/null; then
            COPIED_WITH="copyq"
        elif echo "$ROOT_PW" | wl-copy 2>/dev/null; then
            COPIED_WITH="wl-copy"
        fi

        if [ -n "$COPIED_WITH" ]; then
            echo "  [OK] Root password copied to clipboard ($COPIED_WITH)"
            echo "       Paste with \$mod+V (Sway) at the 'su -' prompt"
        else
            echo "  [--] Could not copy to clipboard -- no clipboard manager"
            echo "       Select and copy the password above manually"
        fi
    else
        echo "  [--] No root_password= line found in recovery credentials"
        echo "       Select and copy the password above manually"
    fi
    echo ""
    echo "  After logging in as root, change to a simpler password:"
    echo "    su -"
    echo "    passwd"
    echo "    (enter new password twice)"
else
    echo "[$(date -u '+%Y-%m-%d %H:%M:%S UTC')] unseal: FAILED" >> "$SEAL_DIR/seal.log"
    echo ""
    echo "  [FAIL] Decryption FAILED -- see $SEAL_DIR/seal.log"
    echo ""
    exit 1
fi
