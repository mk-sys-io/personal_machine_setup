#!/usr/bin/env python3
"""notify-observe — temporary D-Bus observer for notification traffic (Phase 1).

Bus-level monitor (BecomeMonitor). Logs Notify method calls, NotificationClosed
and ActionInvoked signals, and daemon owner changes to the day JSONL file.
Self-contained envelope code (temporary component — see Phase 5 dismantling).
"""

import json
import os
import signal
import time
from pathlib import Path

import dbus
import dbus.mainloop.glib
from gi.repository import GLib  # type: ignore[import]

STATE_DIR = Path(os.path.expanduser("~/.local/state/notify-debug"))
NOTIF_IFACE = "org.freedesktop.Notifications"
DBUS_IFACE = "org.freedesktop.DBus"
DBUS_PATH = "/org/freedesktop/DBus"
NOTIF_PATH = "/org/freedesktop/Notifications"


def boot_id() -> str:
    try:
        return Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    except OSError:
        return ""


def day_path(ts: float) -> Path:
    import datetime

    d = datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).date().isoformat()
    return STATE_DIR / f"{d}.jsonl"


def write_record(rec: dict) -> None:
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        p = day_path(rec["rt"])
        line = json.dumps(rec, separators=(",", ":")) + "\n"
        import fcntl

        with p.open("a", encoding="utf-8") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            f.write(line)
            f.flush()
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass


def envelope(kind: str, event_id) -> dict:
    rt = time.time()
    mono = time.monotonic()
    return {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(rt)) + f".{int((rt % 1) * 1000):03d}Z",
        "rt": rt,
        "mono": mono,
        "boot_id": boot_id(),
        "kind": kind,
        "src": "notify-observe",
        "app": None,
        "event_id": event_id,
    }


# in-memory map daemon notification id -> event_id (process lifetime)
_id_to_event: dict[int, str | None] = {}


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
    # Notify(app_name, replaces_id, app_icon, summary, body, actions, hints, timeout)
    app_name = args[0] if len(args) > 0 else None
    replaces_id = args[1] if len(args) > 1 else 0
    summary = args[3] if len(args) > 3 else None
    body = args[4] if len(args) > 4 else None
    actions = args[5] if len(args) > 5 else []
    hints = args[6] if len(args) > 6 else {}
    timeout = args[7] if len(args) > 7 else -1
    event_id = hint_from_hints(hints)
    rec = envelope("observed_notify", event_id)
    rec["app"] = app_name
    rec["summary"] = summary
    rec["body"] = body
    rec["replaces_id"] = replaces_id
    rec["actions"] = [str(a) for a in (actions or [])]
    rec["hints"] = {str(k): str(v) for k, v in (hints or {}).items()}
    rec["timeout_ms"] = timeout
    rec["sender"] = msg.get_sender()
    # daemon assigns id later; we cannot see reply. Track by hint only.
    write_record(rec)


def handle_closed(msg) -> None:
    try:
        args = list(msg.get_args_list())
    except Exception:
        return
    nid = args[0] if len(args) > 0 else None
    reason = args[1] if len(args) > 1 else None
    event_id = _id_to_event.get(nid) if isinstance(nid, int) else None
    rec = envelope("observed_close", event_id)
    rec["notification_id"] = nid
    rec["reason"] = reason
    rec["action_invoked"] = False
    write_record(rec)


def handle_action(msg) -> None:
    try:
        args = list(msg.get_args_list())
    except Exception:
        return
    nid = args[0] if len(args) > 0 else None
    action_key = args[1] if len(args) > 1 else None
    event_id = _id_to_event.get(nid) if isinstance(nid, int) else None
    rec = envelope("observed_close", event_id)
    rec["notification_id"] = nid
    rec["reason"] = None
    rec["action_invoked"] = True
    rec["action_key"] = action_key
    write_record(rec)


def handle_name_owner(msg) -> None:
    try:
        args = list(msg.get_args_list())
    except Exception:
        return
    if len(args) < 3:
        return
    name, old_owner, new_owner = args[0], args[1], args[2]
    if str(name) != NOTIF_IFACE:
        return
    rec = envelope("daemon_owner", None)
    rec["name"] = str(name)
    rec["old_owner"] = str(old_owner) if old_owner else None
    rec["new_owner"] = str(new_owner) if new_owner else None
    write_record(rec)


def msg_cb(conn, msg):
    try:
        iface = msg.get_interface()
        member = msg.get_member()
        if iface == NOTIF_IFACE and member == "Notify":
            handle_notify(msg)
        elif iface == NOTIF_IFACE and member == "NotificationClosed":
            handle_closed(msg)
        elif iface == NOTIF_IFACE and member == "ActionInvoked":
            handle_action(msg)
        elif iface == DBUS_IFACE and member == "NameOwnerChanged":
            handle_name_owner(msg)
    except Exception:
        pass
    return None


def main() -> None:
    dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
    bus = dbus.SessionBus()
    proxy = bus.get_object(DBUS_IFACE, DBUS_PATH)
    mon = dbus.Interface(proxy, "org.freedesktop.DBus.Monitoring")
    rules = [
        "type='method_call',interface='org.freedesktop.Notifications',member='Notify'",
        "type='signal',interface='org.freedesktop.Notifications',member='NotificationClosed'",
        "type='signal',interface='org.freedesktop.Notifications',member='ActionInvoked'",
        "type='signal',sender='org.freedesktop.DBus',interface='org.freedesktop.DBus',member='NameOwnerChanged'",
    ]
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
