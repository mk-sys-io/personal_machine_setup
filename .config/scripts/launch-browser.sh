#!/bin/bash
export OZONE_PLATFORM=wayland
PROFILE_DIR="$HOME/.config/chromium-minimal"
mkdir -p "$PROFILE_DIR"
chromium --user-data-dir="$PROFILE_DIR" --no-first-run --no-default-browser-check --password-store=basic
