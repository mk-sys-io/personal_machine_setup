#!/bin/sh
# wlsunset-resume.sh — Re-evaluate night light state after suspend/resume.
# Deployed to /usr/lib/systemd/system-sleep/ by install.sh.
# $1 = "pre" (before suspend) or "post" (after resume)
# $2 = "suspend" | "hibernate" | "hybrid-sleep"
if [ "$1" = "post" ]; then
    pkill -USR1 -f wlsunset-notify.py 2>/dev/null || true
fi
