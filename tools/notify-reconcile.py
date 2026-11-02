#!/usr/bin/env python3
"""notify-reconcile — temporary reconciler for notification logging (Phase 1).

Reads day JSONL file(s), joins `attempt` <-> `observed_notify` by the
`swaync-log-id` hint, classifies each attempt, and appends `occurrence`
records (including a per-day summary) to the day file.

Usage:
  notify-reconcile            # today (UTC)
  notify-reconcile --day YYYY-MM-DD
  notify-reconcile --all      # all day files present
Self-contained envelope code (temporary component).
"""

import argparse
import json
import os
import re
import time
from pathlib import Path

STATE_DIR = Path(os.path.expanduser("~/.local/state/notify-debug"))
CONFIG_PATH = Path(os.path.expanduser("~/.config/sway/swaync/config.json"))
GRACE_SECONDS = 60.0
WRONG_TIME_THRESHOLD = 5.0


def boot_id() -> str:
    try:
        return Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    except OSError:
        return ""


def day_path_for(d: str) -> Path:
    return STATE_DIR / f"{d}.jsonl"


def load_day(d: str) -> list[dict]:
    p = day_path_for(d)
    out = []
    if not p.exists():
        return out
    with p.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except (json.JSONDecodeError, ValueError):
                continue
    return out


def all_days() -> list[str]:
    if not STATE_DIR.exists():
        return []
    return sorted(p.stem for p in STATE_DIR.glob("*.jsonl"))


def prev_day(d: str) -> str | None:
    import datetime

    try:
        dt = datetime.date.fromisoformat(d)
    except ValueError:
        return None
    return (dt - datetime.timedelta(days=1)).isoformat()


def write_record(rec: dict) -> None:
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        import datetime

        d = datetime.datetime.fromtimestamp(rec["rt"], datetime.timezone.utc).date().isoformat()
        p = STATE_DIR / f"{d}.jsonl"
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
        "src": "notify-reconcile",
        "app": None,
        "event_id": None,
    }


def load_visibility_rules() -> tuple[dict, bool]:
    """Return (rules dict name-> {state, pattern}, config_ok)."""
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return {}, False
    rules = data.get("notification-visibility") or {}
    compiled = {}
    for name, spec in rules.items():
        pat = (spec or {}).get("app-name")
        try:
            rx = re.compile(pat) if pat else None
        except re.error:
            rx = None
        compiled[name] = {"state": (spec or {}).get("state"), "pattern": rx, "raw": spec}
    return compiled, True


def match_visibility(app: str | None, rules: dict) -> str | None:
    if not app:
        return None
    for name, spec in rules.items():
        rx = spec.get("pattern")
        if rx is not None and rx.search(app):
            return name
    return None


def as_of_state(observed_rt: float, states: list[dict]) -> dict[str, object] | None:
    # latest swaync_state with rt <= observed_rt
    cand = [s for s in states if s.get("rt", 0) <= observed_rt]
    if not cand:
        return None
    cand.sort(key=lambda s: s.get("rt", 0))
    return cand[-1]


def reconcile_day(d: str, rules: dict, config_ok: bool) -> dict:
    records = load_day(d)
    # also load previous day for boundary stitching
    pd = prev_day(d)
    prev_records = load_day(pd) if pd else []
    all_recs = prev_records + records

    attempts = [r for r in all_recs if r.get("kind") == "attempt"]
    observed = [r for r in all_recs if r.get("kind") == "observed_notify"]
    states = [r for r in all_recs if r.get("kind") == "swaync_state"]

    # config missing occurrence
    if not config_ok:
        occ = envelope("occurrence")
        occ["day"] = d
        occ["anomaly"] = "config_missing"
        occ["detail"] = f"deployed config unreadable: {CONFIG_PATH}"
        write_record(occ)

    counts = {"ok": 0, "missing_fire": 0, "unknown_fire": 0, "not_shown": 0,
              "wrong_time": 0, "duplicate": 0, "id_anomaly": 0, "stale_reason": 0}

    seen_event_ids: dict[str, int] = {}
    for ob in observed:
        eid = ob.get("event_id")
        if eid:
            seen_event_ids[eid] = seen_event_ids.get(eid, 0) + 1

    for att in attempts:
        # only classify attempts whose rt falls in this day (avoid double-processing prev day)
        import datetime

        att_day = datetime.datetime.fromtimestamp(att.get("rt", 0), datetime.timezone.utc).date().isoformat()
        if att_day != d:
            continue
        eid = att.get("event_id")
        expected_at = att.get("expected_at") or (att.get("trigger") or {}).get("expected_at")
        matching = [o for o in observed if o.get("event_id") == eid and eid is not None]
        # day-boundary stitch: allow observed in next day within grace
        # (already included via prev/next? observed list is current+prev only; add next day observed)
        nd = None
        try:
            import datetime as _dt

            nd = (_dt.date.fromisoformat(d) + _dt.timedelta(days=1)).isoformat()
        except ValueError:
            nd = None
        if nd:
            for o in load_day(nd):
                if o.get("kind") == "observed_notify" and o.get("event_id") == eid and eid is not None:
                    matching.append(o)

        now_ts = time.time()
        # duplicate check
        is_dup = eid is not None and seen_event_ids.get(eid, 0) > 1

        if not matching:
            # grace window
            att_rt = att.get("rt", 0)
            if now_ts - att_rt < GRACE_SECONDS:
                # still pending; skip classification this run
                continue
            occ = envelope("occurrence")
            occ["day"] = d
            occ["anomaly"] = "missing_fire"
            occ["event_id"] = eid
            occ["app"] = att.get("app")
            occ["attempt_rt"] = att_rt
            occ["stale_reason"] = "no_observed_notify_within_60s"
            occ["attempt"] = att
            write_record(occ)
            counts["missing_fire"] += 1
            counts["stale_reason"] += 1
            continue

        # id_anomaly: observed without hint but same app/summary as an attempt
        for ob in matching:
            if ob.get("event_id") is None:
                occ = envelope("occurrence")
                occ["day"] = d
                occ["anomaly"] = "id_anomaly"
                occ["app"] = ob.get("app")
                occ["detail"] = "observed Notify missing swaync-log-id hint"
                write_record(occ)
                counts["id_anomaly"] += 1

        # duplicate
        if is_dup:
            occ = envelope("occurrence")
            occ["day"] = d
            occ["anomaly"] = "duplicate"
            occ["event_id"] = eid
            occ["count"] = seen_event_ids.get(eid or "", 0)
            write_record(occ)
            counts["duplicate"] += 1

        # wrong_time / not_shown / ok
        for ob in matching:
            observed_rt = ob.get("rt", 0)
            delta = None
            if expected_at is not None:
                try:
                    delta = observed_rt - float(expected_at)
                except (TypeError, ValueError):
                    delta = None

            st = as_of_state(observed_rt, states)
            dnd = (st or {}).get("dnd") if st else None
            inhibited = (st or {}).get("inhibited") if st else None

            app = att.get("app") or ob.get("app")
            vis_name = match_visibility(app, rules)
            is_transient = vis_name is not None and rules.get(vis_name, {}).get("state") == "transient"

            if dnd is True or inhibited is True:
                anomaly = "not_shown"
            elif delta is not None and abs(delta) > WRONG_TIME_THRESHOLD:
                anomaly = "wrong_time"
            else:
                anomaly = "ok"

            if anomaly == "ok":
                counts["ok"] += 1
                continue
            if anomaly == "not_shown":
                counts["not_shown"] += 1
            elif anomaly == "wrong_time":
                counts["wrong_time"] += 1

            occ = envelope("occurrence")
            occ["day"] = d
            occ["anomaly"] = anomaly
            occ["event_id"] = eid
            occ["app"] = app
            occ["attempt_rt"] = att.get("rt")
            occ["observed_rt"] = observed_rt
            occ["delta_seconds"] = delta
            occ["dnd_at_observed"] = dnd
            occ["inhibited_at_observed"] = inhibited
            occ["visibility_rule"] = vis_name
            occ["transient"] = is_transient
            occ["expected_at"] = expected_at
            write_record(occ)

    # unknown_fire: observed_notify with no matching attempt
    attempt_eids = {a.get("event_id") for a in attempts if a.get("event_id")}
    for ob in observed:
        if ob.get("rt", 0) < 0:
            continue
        ob_day = None
        try:
            import datetime as _dt

            ob_day = _dt.datetime.fromtimestamp(ob.get("rt", 0), _dt.timezone.utc).date().isoformat()
        except (OSError, ValueError):
            ob_day = None
        if ob_day != d:
            continue
        if ob.get("event_id") is not None and ob.get("event_id") in attempt_eids:
            continue
        # only flag if hint absent or unmatched
        if ob.get("event_id") in attempt_eids:
            continue
        occ = envelope("occurrence")
        occ["day"] = d
        occ["anomaly"] = "unknown_fire"
        occ["app"] = ob.get("app")
        occ["summary"] = ob.get("summary")
        occ["observed_rt"] = ob.get("rt")
        occ["event_id"] = ob.get("event_id")
        write_record(occ)
        counts["unknown_fire"] += 1

    # per-day summary
    summary = envelope("occurrence")
    summary["day"] = d
    summary["anomaly"] = "summary"
    summary["counts"] = counts
    summary["attempts"] = len([a for a in attempts if _day_of(a.get("rt", 0)) == d])
    summary["observed"] = len([o for o in observed if _day_of(o.get("rt", 0)) == d])
    write_record(summary)

    return counts


def _day_of(ts) -> str:
    import datetime

    try:
        return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).date().isoformat()
    except (OSError, ValueError):
        return ""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--day", default=None)
    parser.add_argument("--all", action="store_true")
    args = parser.parse_args()

    rules, config_ok = load_visibility_rules()

    if args.all:
        days = all_days()
    elif args.day:
        days = [args.day]
    else:
        import datetime

        days = [datetime.datetime.now(datetime.timezone.utc).date().isoformat()]

    for d in days:
        try:
            reconcile_day(d, rules, config_ok)
        except Exception:
            continue


if __name__ == "__main__":
    main()
