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
    nano "$SEAL_DIR/recovery-credentials"
else
    echo "[$(date -u '+%Y-%m-%d %H:%M:%S UTC')] unseal: FAILED" >> "$SEAL_DIR/seal.log"
    echo ""
    echo "  [FAIL] Decryption FAILED -- see $SEAL_DIR/seal.log"
    echo ""
    exit 1
fi
