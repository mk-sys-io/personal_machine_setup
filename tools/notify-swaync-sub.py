#!/usr/bin/env python3
"""notify-swaync-sub — temporary swaync aggregate-state subscriber (Phase 1).

Wraps `swaync-client -s` and envelope-wraps each aggregate frame as
kind=swaync_state ({count, dnd, visible, inhibited}). Reconnects on child exit.
Self-contained envelope code (temporary component).
"""

import json
import os
import signal
import subprocess
import time
from pathlib import Path

STATE_DIR = Path(os.path.expanduser("~/.local/state/notify-debug"))


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


def envelope(kind: str) -> dict:
    rt = time.time()
    mono = time.monotonic()
    return {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(rt)) + f".{int((rt % 1) * 1000):03d}Z",
        "rt": rt,
        "mono": mono,
        "boot_id": boot_id(),
        "kind": kind,
        "src": "notify-swaync-sub",
        "app": None,
        "event_id": None,
    }


def run_once() -> None:
    proc = subprocess.Popen(
        ["swaync-client", "-s"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        bufsize=1,
    )
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                frame = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            rec = envelope("swaync_state")
            rec["count"] = frame.get("count")
            rec["dnd"] = frame.get("dnd")
            rec["visible"] = frame.get("visible")
            rec["inhibited"] = frame.get("inhibited")
            rec["raw"] = frame
            write_record(rec)
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()


def main() -> None:
    backoff = 1
    while True:
        try:
            run_once()
            backoff = 1
        except Exception:
            time.sleep(backoff)
            backoff = min(backoff * 2, 30)


if __name__ == "__main__":
    # handle SIGTERM
    import signal

    def _stop(*_a):
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    main()
