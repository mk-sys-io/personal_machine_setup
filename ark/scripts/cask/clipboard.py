#!/usr/bin/python3
"""Clipboard clearing for the cask/uncask credential flows.

Extracted from cask_lib.py so all clipboard knowledge — cliphist/wl-copy,
Wayland session discovery, and the XDG env needed to reach the live session —
lives in one module. Swap or extend the tools here without touching the cask
logic. Every failure path is logged via opslog; nothing is silent.
"""

from __future__ import annotations

import os
import pwd
import subprocess

import opslog

# ── User/identity (gomplate-rendered) ─────────────────────────────────────────
# Mirrors cask_lib's lookup: clipboard ops run as the target user via sudo -u.

MIKE: pwd.struct_passwd = pwd.getpwnam("{{ .Env.USERNAME }}")
MIKE_UID: int = MIKE.pw_uid
HOME_DIR: str = MIKE.pw_dir

# ── Adapter PATH resolution ──────────────────────────────────────────────────
# Ensure etc/ark/adapters/ is in PATH so the clipboard-clear adapter is found.

ARK_LIB: str = os.environ.get("ARK_LIB_PATH", "/usr/local/lib/ark")
os.environ["PATH"] = f"{ARK_LIB}:{os.environ['PATH']}"

PRESERVE_SESSION_ENV = "--preserve-env=XDG_RUNTIME_DIR,WAYLAND_DISPLAY,XDG_DATA_HOME,DBUS_SESSION_BUS_ADDRESS"


# ── Wayland session discovery ────────────────────────────────────────────────


def discover_session() -> tuple[str, str, str, str]:
    """Discover the target user's Wayland/DBus session env from a live
    sway/waybar process's ``/proc/<pid>/environ``.

    Returns ``(dbus_addr, wayland_display, xdg_data_home, xdg_config_home)``;
    entries are empty strings when they can't be found, so clipboard ops can
    degrade to a logged skip instead of guessing at the session env.
    """
    dbus_addr = ""
    wayland_display = ""
    xdg_data_home = ""
    xdg_config_home = ""

    for proc in ["sway", "waybar"]:
        try:
            r = subprocess.run(
                ["pgrep", "-u", str(MIKE_UID), "-x", proc],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            if not r.stdout.strip():
                continue
            pid = r.stdout.strip().split("\n")[0]
            env_path = f"/proc/{pid}/environ"
            if not os.path.isfile(env_path) or not os.access(env_path, os.R_OK):
                continue
            with open(env_path, "rb") as f:
                raw = f.read()
            for entry in raw.split(b"\0"):
                if not entry:
                    continue
                try:
                    dec = entry.decode("utf-8")
                except UnicodeDecodeError:
                    continue
                if dec.startswith("DBUS_SESSION_BUS_ADDRESS="):
                    dbus_addr = dec.split("=", 1)[1]
                elif dec.startswith("WAYLAND_DISPLAY="):
                    wayland_display = dec.split("=", 1)[1]
                elif dec.startswith("XDG_DATA_HOME="):
                    xdg_data_home = dec.split("=", 1)[1]
                elif dec.startswith("XDG_CONFIG_HOME="):
                    xdg_config_home = dec.split("=", 1)[1]
            if dbus_addr and wayland_display:
                break
        except (subprocess.TimeoutExpired, OSError) as e:
            opslog.warn(f"discover_session: {e}")
            continue

    xdg_config_home = xdg_config_home or os.path.join(HOME_DIR, ".config")
    xdg_data_home = xdg_data_home or os.path.join(HOME_DIR, ".local", "share")
    return dbus_addr, wayland_display, xdg_data_home, xdg_config_home


# ── Clipboard clearing ───────────────────────────────────────────────────────


def clear_clipboard(purge: bool = False) -> None:
    opslog.set_step("Clearing clipboard history")

    # Simple clear: delegate to adapter (handles cliphist/wl-copy detection)
    if not purge:
        try:
            r = subprocess.run(
                ["clipboard-clear"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            if r.returncode == 0:
                opslog.ok("Clipboard cleared via adapter")
            else:
                opslog.warn(f"clipboard-clear failed: {r.stderr.strip()}")
        except (subprocess.TimeoutExpired, OSError) as e:
            opslog.warn(f"clipboard-clear failed: {e}")
        return

    # Purge mode: run cliphist wipe + wl-copy --clear as the target user with
    # the live session env. Without HOME/XDG_DATA_HOME the tools resolve their
    # DB against the wrong user and wipe nothing while logging success.
    dbus_addr, wayland_display, xdg_data_home, _ = discover_session()
    if not wayland_display:
        opslog.warn(
            "No Wayland session discovered — wipe may not reach the live session"
        )

    env = {"HOME": HOME_DIR, "XDG_RUNTIME_DIR": f"/run/user/{MIKE_UID}"}
    if wayland_display:
        env["WAYLAND_DISPLAY"] = wayland_display
    if xdg_data_home:
        env["XDG_DATA_HOME"] = xdg_data_home

    # cliphist wipe (primary)
    try:
        r = subprocess.run(
            ["sudo", "-H", "-u", f"#{MIKE_UID}", PRESERVE_SESSION_ENV,
             "cliphist", "wipe"],
            env=env,
            capture_output=True,
            timeout=10,
            check=False,
        )
        if r.returncode == 0:
            opslog.ok("cliphist history wiped")
        else:
            opslog.warn(f"cliphist wipe failed: {r.stderr.strip()}")
    except FileNotFoundError:
        opslog.warn("cliphist not installed — skipping")
    except (subprocess.TimeoutExpired, OSError) as e:
        opslog.warn(f"cliphist wipe failed: {e}")

    # wl-copy --clear (fallback / belt-and-suspenders)
    try:
        r = subprocess.run(
            ["sudo", "-H", "-u", f"#{MIKE_UID}", PRESERVE_SESSION_ENV,
             "wl-copy", "--clear"],
            env=env,
            capture_output=True,
            timeout=10,
            check=False,
        )
        if r.returncode == 0:
            opslog.ok("Wayland clipboard cleared")
        else:
            opslog.warn(f"wl-copy --clear failed: {r.stderr.strip()}")
    except (subprocess.TimeoutExpired, OSError) as e:
        opslog.warn(f"wl-copy --clear failed: {e}")
