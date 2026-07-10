#!/usr/bin/env python3
"""Discover active session type. Swap WM detection here, not in seal."""
import subprocess, sys

# Ordered by preference — add new WMs here
sessions = {
    "sway":     "pgrep -x sway",
    "hyprland": "pgrep -x Hyprland",
    "river":    "pgrep -x river",
}

for name, cmd in sessions.items():
    result = subprocess.run(cmd, shell=True, capture_output=True)
    if result.returncode == 0:
        print(name)
        sys.exit(0)

print("unknown")
sys.exit(1)
