#!/usr/bin/env python3
"""
wlsunset-notify.py — Single-owner daemon for wlsunset blue-light filter.

Manages wlsunset lifecycle, sends notifications on automatic transitions
(6am/5pm) and on resume from suspend. Responds to SIGUSR2 for manual
toggle via waybar.

Signals:
  SIGUSR1 — resume from suspend (sent by /usr/lib/systemd/system-sleep/wlsunset-resume.sh)
  SIGUSR2 — toggle manual override (sent by waybar system menu)

Usage:
  exec_always ~/.config/sway/scripts/wlsunset-notify.py   # in sway config
"""

import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timedelta

# ---------------------------------------------------------------------------
# Configuration — edit these values to adjust temperature and transition times
# ---------------------------------------------------------------------------

WLSUNSET_ARGS = ["wlsunset", "-t", "3000", "-T", "7100", "-S", "06:00", "-s", "17:00", "-d", "180"]

STATE_FILE = os.path.expanduser("~/.config/sway/scripts/wlsunset.state")

# Derived from WLSUNSET_ARGS — single source of truth
WARM_TEMP = int(WLSUNSET_ARGS[WLSUNSET_ARGS.index("-t") + 1])
COOL_TEMP = int(WLSUNSET_ARGS[WLSUNSET_ARGS.index("-T") + 1])
SUNRISE_HOUR = int(WLSUNSET_ARGS[WLSUNSET_ARGS.index("-S") + 1].split(":")[0])
SUNSET_HOUR = int(WLSUNSET_ARGS[WLSUNSET_ARGS.index("-s") + 1].split(":")[0])

# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

night_active = False
forcing = False
next_recovery = 0.0
wlsunset_proc = None  # Popen object for wlsunset child

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def is_night():
    h = datetime.now().hour
    return h >= SUNSET_HOUR or h < SUNRISE_HOUR


def start_wlsunset():
    global wlsunset_proc
    if wlsunset_proc is not None and wlsunset_proc.poll() is None:
        return
    wlsunset_proc = subprocess.Popen(WLSUNSET_ARGS)
    write_state("on")


def stop_wlsunset():
    global wlsunset_proc
    if wlsunset_proc is not None and wlsunset_proc.poll() is None:
        wlsunset_proc.terminate()
        try:
            wlsunset_proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            wlsunset_proc.kill()
            wlsunset_proc.wait()
    wlsunset_proc = None
    write_state("off")
    time.sleep(0.5)


def notify(msg):
    subprocess.run(
        ["notify-send", "-a", "wlsunset", "-e", "-u", "low", "-t", "3000", msg],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def read_state():
    try:
        with open(STATE_FILE) as f:
            return f.read().strip()
    except (OSError, IOError):
        return "on"


def write_state(state):
    try:
        with open(STATE_FILE, "w") as f:
            f.write(state + "\n")
    except OSError:
        pass


def seconds_until_next_transition():
    now = datetime.now()
    h = now.hour
    if is_night():
        target = now.replace(hour=SUNRISE_HOUR, minute=0, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
    else:
        target = now.replace(hour=SUNSET_HOUR, minute=0, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
    return max(1, int((target - now).total_seconds()))


def calculate_next_dusk():
    now = datetime.now().replace(microsecond=0)
    today = now.replace(hour=SUNSET_HOUR, minute=0, second=0, microsecond=0)
    if now <= today:
        return today.timestamp()
    return (today + timedelta(days=1)).timestamp()

# ---------------------------------------------------------------------------
# Cleanup — kill existing daemon and orphaned wlsunset on startup
# ---------------------------------------------------------------------------

def cleanup_existing():
    """Kill any existing daemon Python instance and orphaned wlsunset."""
    my_pid = os.getpid()

    try:
        result = subprocess.run(
            ["pgrep", "-f", "python3.*wlsunset-notify"],
            capture_output=True, text=True, timeout=2
        )
        for line in result.stdout.strip().split("\n"):
            if not line.strip():
                continue
            try:
                pid = int(line.strip())
                if pid != my_pid:
                    os.kill(pid, signal.SIGTERM)
            except (ProcessLookupError, ValueError):
                pass
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    for _ in range(10):
        time.sleep(0.1)
        try:
            result = subprocess.run(
                ["pgrep", "-f", "python3.*wlsunset-notify"],
                capture_output=True, text=True, timeout=1
            )
            pids = [l.strip() for l in result.stdout.strip().split("\n") if l.strip()]
            other_pids = [int(p) for p in pids if int(p) != my_pid]
            if not other_pids:
                break
        except (subprocess.TimeoutExpired, FileNotFoundError, ValueError):
            break

    subprocess.run(["pkill", "-x", "wlsunset"], timeout=2)
    time.sleep(0.5)

# ---------------------------------------------------------------------------
# Signal handlers
# ---------------------------------------------------------------------------

def handle_sigusr1(signum, frame):
    """Resume from suspend — clear forcing, ensure wlsunset runs."""
    global night_active, forcing
    forcing = False
    start_wlsunset()
    wanted = is_night()
    if wanted != night_active:
        if wanted:
            notify("Blue light filter transitioning to night mode (3 min)")
        else:
            notify("Blue light filter transitioning to day mode (3 min)")
        night_active = wanted


def handle_sigusr2(signum, frame):
    """Waybar toggle — flip manual override."""
    global forcing, night_active, next_recovery
    if not forcing:
        forcing = True
        next_recovery = calculate_next_dusk()
        stop_wlsunset()
    else:
        forcing = False
        start_wlsunset()
        night_active = is_night()

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    global night_active, forcing, next_recovery

    cleanup_existing()
    signal.signal(signal.SIGUSR1, handle_sigusr1)
    signal.signal(signal.SIGUSR2, handle_sigusr2)

    night_active = is_night()
    saved = read_state()
    if saved == "off":
        forcing = True
        next_recovery = calculate_next_dusk()
    else:
        start_wlsunset()

    while True:
        if forcing:
            if time.time() >= next_recovery:
                forcing = False
                start_wlsunset()
                night_active = is_night()
                if night_active:
                    notify("Blue light filter transitioning to night mode (3 min)")
            else:
                sleep_sec = min(60, int(next_recovery - time.time()))
                try:
                    time.sleep(max(1, sleep_sec))
                except ValueError:
                    continue
        else:
            sleep_sec = seconds_until_next_transition()
            try:
                time.sleep(sleep_sec)
            except ValueError:
                continue

            if not forcing:
                wanted = is_night()
                if wanted != night_active and not forcing:
                    if wanted:
                        notify("Blue light filter transitioning to night mode (3 min)")
                    else:
                        notify("Blue light filter transitioning to day mode (3 min)")
                    night_active = wanted


if __name__ == "__main__":
    main()
