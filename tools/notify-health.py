#!/usr/bin/env python3
"""notify-health — permanent slim passive observer (sole surviving component).

Sole surviving component after dismantling. Monitors the session bus for
Notify calls and subscribes to swaync aggregate state; writes metadata-only
records (app_name, ts, fired/blocked, dnd_at_fire) to
~/.local/state/notify-health.jsonl. Self-contained envelope code.
"""

import json
import os
import signal
import subprocess
import threading
import time
from pathlib import Path

import dbus
import dbus.mainloop.glib
from gi.repository import GLib  # type: ignore[import]

HEALTH_FILE = Path(os.path.expanduser("~/.local/state/notify-health.jsonl"))
NOTIF_IFACE = "org.freedesktop.Notifications"


def boot_id() -> str:
    try:
        return Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    except OSError:
        return ""


def write_health(rec: dict) -> None:
    try:
        HEALTH_FILE.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(rec, separators=(",", ":")) + "\n"
        import fcntl

        with HEALTH_FILE.open("a", encoding="utf-8") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            f.write(line)
            f.flush()
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass


def envelope_health() -> dict:
    rt = time.time()
    mono = time.monotonic()
    return {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(rt)) + f".{int((rt % 1) * 1000):03d}Z",
        "rt": rt,
        "mono": mono,
        "boot_id": boot_id(),
        "kind": "health",
        "src": "notify-health",
        "app": None,
        "event_id": None,
    }


# Shared DND state from swaync subscriber
_state_lock = threading.Lock()
_dnd_state = {"dnd": None, "inhibited": None, "rt": 0.0}


def subscriber_loop() -> None:
    backoff = 1
    while True:
        try:
            proc = subprocess.Popen(
                ["swaync-client", "-s"],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                bufsize=1,
            )
            assert proc.stdout is not None
            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    frame = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                with _state_lock:
                    _dnd_state["dnd"] = frame.get("dnd")
                    _dnd_state["inhibited"] = frame.get("inhibited")
                    _dnd_state["rt"] = time.time()
            backoff = 1
        except Exception:
            time.sleep(backoff)
            backoff = min(backoff * 2, 30)


def hint_from_hints(hints) -> str | None:
    try:
        for k, v in (hints or {}).items():
            if str(k) == "swaync-log-id":
                return str(v)
    except Exception:
        pass
    return None


def handle_notify(msg) -> None:
    try:
        args = list(msg.get_args_list())
    except Exception:
        return
    app_name = args[0] if len(args) > 0 else None
    hints = args[6] if len(args) > 6 else {}
    with _state_lock:
        dnd = _dnd_state["dnd"]
        inhibited = _dnd_state["inhibited"]
    blocked = bool(dnd) or bool(inhibited)
    rec = envelope_health()
    rec["app"] = app_name  # app_name stored as app
    rec["app_name"] = app_name
    rec["fired"] = not blocked
    rec["blocked"] = blocked
    rec["dnd_at_fire"] = dnd
    rec["inhibited_at_fire"] = inhibited
    rec["event_id"] = hint_from_hints(hints)
    write_health(rec)


def msg_cb(conn, msg):
    try:
        if msg.get_interface() == NOTIF_IFACE and msg.get_member() == "Notify":
            handle_notify(msg)
    except Exception:
        pass
    return None


def main() -> None:
    t = threading.Thread(target=subscriber_loop, daemon=True)
    t.start()

    dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
    bus = dbus.SessionBus()
    proxy = bus.get_object("org.freedesktop.DBus", "/org/freedesktop/DBus")
    mon = dbus.Interface(proxy, "org.freedesktop.DBus.Monitoring")
    rules = ["type='method_call',interface='org.freedesktop.Notifications',member='Notify'"]
    mon.BecomeMonitor(rules, 0)
    bus.add_message_filter(msg_cb)

    loop = GLib.MainLoop()

    def _stop(*_a):
        loop.quit()

    for sig in (signal.SIGTERM, signal.SIGINT):
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, sig, _stop)
    loop.run()


if __name__ == "__main__":
    main()
