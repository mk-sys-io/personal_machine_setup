from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import TypedDict

import mode
import netmgr
import netmgr.health
import netmgr.namespace
import netmgr.wrappers
from cask import lib

from ark import ARK_DATA_DIR, LOG_COMMANDS, STATE_FILE, _format_remaining, _get_user

_STATUS_SECTIONS = (
    ("mode_and_access", "Mode and access"),
    ("network", "Network enforcement"),
    ("namespace", "Internet namespace"),
    ("timelock", "Timelock"),
    ("browser_policies", "Browser policies"),
)

_GLYPH = {"pass": "\u2713", "fail": "\u2717", "warn": "\u26a0", "info": "\u2139"}
_ANSI = {"pass": "\033[32m", "fail": "\033[31m", "warn": "\033[33m",
         "info": "\033[36m"}
_RESET = "\033[0m"
_TIMELOCK_GLYPH = "\u23f3"


class StatusRow(TypedDict):
    state: str
    label: str
    value: str
    hint: str | None
    detail: list[str] | None


def _status_row(state: str, label: str, value: str,
                hint: str | None = None,
                detail: list[str] | None = None) -> StatusRow:
    return {"state": state, "label": label, "value": value, "hint": hint,
            "detail": detail}


def _user_in_sudo(user: str) -> bool | None:
    """True when `user` is in the sudo group; None when unprobeable."""
    try:
        r = subprocess.run(["groups", user], capture_output=True, text=True,
                           check=False, timeout=10)
    except (subprocess.SubprocessError, OSError):
        return None
    return "sudo" in r.stdout.split()


def _svc_active(unit: str) -> bool:
    try:
        r = subprocess.run(["systemctl", "is-active", "--quiet", unit],
                           capture_output=True, check=False)
    except (subprocess.SubprocessError, OSError):
        return False
    return r.returncode == 0


def _enable_end_status() -> str | None:
    """enable.log's gate-protocol END marker: "OK"/"FAILED", None if absent.

    Reads the `=== END enable: <status> ===` marker, not incidental log
    content — the same markers that form the abort-gate protocol. Only ever
    used to classify an ARMED gate (enable.json present); never as the gate
    verdict itself. Unreadable/missing log → None (caller falls back to the
    mode-based classification).
    """
    path = Path(f"{ARK_DATA_DIR}/logs/enable.log")
    if not path.is_file():
        return None
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return None
    marker = re.compile(r"^=== END enable: (\w+) ===$")
    for line in lines:
        match = marker.match(line)
        if match:
            return match.group(1)
    return None


def _last_transition() -> tuple[str, str] | None:
    """Most recent enable/disable/abort END marker: (command, status).

    Presentation-only — never affects the verdict or exit code. No END
    marker in the newest log means the transition was interrupted.
    """
    logs_dir = Path(f"{ARK_DATA_DIR}/logs")
    newest: Path | None = None
    newest_mtime = -1.0
    for name in LOG_COMMANDS:
        path = logs_dir / f"{name}.log"
        if not path.is_file():
            continue
        mtime = path.stat().st_mtime
        if mtime > newest_mtime:
            newest = path
            newest_mtime = mtime
    if newest is None:
        return None
    try:
        lines = newest.read_text().splitlines()
    except OSError:
        return None
    marker = re.compile(r"^=== END (\w+): (\w+) ===$")
    for line in lines:
        match = marker.match(line)
        if match:
            return (match.group(1), match.group(2))
    return (newest.stem, "interrupted")


def cmd_status(args: argparse.Namespace) -> int:
    """One-shot live status report. Read-only: never configures opslog (a
    session here would create a stray status.log). Exit 0 when no enforced
    check fails."""
    now = int(time.time())
    checked_at = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(now))
    user = _get_user()

    try:
        current_mode = mode.read()
    except mode.ModeError as e:
        _render_status_text({k: [] for k, _ in _STATUS_SECTIONS}
            | {"mode_and_access": [_status_row(
                "fail", "Mode", "unreadable", hint=str(e))]},
            [], None, {"pass": 0, "warn": 0, "fail": 1, "info": 0},
            checked_at=checked_at, color=sys.stdout.isatty()
            and not args.plain, verbose=args.verbose)
        return 1

    restricted = current_mode != "unrestricted"
    summary: dict[str, int] = {"pass": 0, "warn": 0, "fail": 0, "info": 0}
    sections: dict[str, list[StatusRow]] = {k: [] for k, _ in _STATUS_SECTIONS}

    def add(section: str, state: str, label: str, value: str,
            hint: str | None = None,
            detail: list[str] | None = None) -> None:
        sections[section].append(_status_row(state, label, value, hint, detail))
        summary[state] += 1

    # ── Mode and access ──
    if restricted:
        add("mode_and_access", "pass", "Mode",
            f"{current_mode} lockdown active")
    else:
        add("mode_and_access", "pass", "Mode",
            "Unrestricted — lockdown open")
    in_sudo = _user_in_sudo(user)
    if in_sudo is None:
        add("mode_and_access", "warn", "Sudo", "unprobeable",
            hint=f"could not query groups for {user}")
    elif in_sudo and restricted:
        add("mode_and_access", "fail", "Sudo",
            "member of sudo — should be removed",
            hint="run: ark disable")
    elif in_sudo:
        add("mode_and_access", "pass", "Sudo", "member of sudo (expected)")
    elif restricted:
        add("mode_and_access", "pass", "Sudo",
            "removed — admin commands stay locked")
    else:
        add("mode_and_access", "warn", "Sudo",
            "not in sudo group — expected in unrestricted",
            hint="run: ark disable to restore sudo")

    # Abort gate: enable.json existence is the authoritative oracle (primary);
    # mode + the enable END marker only classify an armed gate (secondary).
    abort_armed = Path(STATE_FILE).is_file()
    if not abort_armed:
        add("mode_and_access", "pass", "Abort gate", "disarmed — no rollback pending")
    else:
        end = _enable_end_status()
        if end == "OK":
            add("mode_and_access", "fail", "Abort gate",
                "ARMED — gate survived a completed enable",
                hint="enable.json exists despite 'END enable: OK' — the final "
                     "state delete was lost (reboot durability). "
                     "'ark abort' would roll back a working lockdown.",
                detail=["END enable: OK logged, but enable.json still present",
                        "run: sudo ark abort to clear (restores snapshot)"])
        elif end == "FAILED" or restricted:
            add("mode_and_access", "warn", "Abort gate",
                "ARMED — rollback protection live",
                hint="a snapshot exists; 'ark abort' restores it. Gate "
                     "clears when the enable completes or on disable.",
                detail=["enable interrupted or mid-flight — abort to roll back"])
        else:
            add("mode_and_access", "fail", "Abort gate",
                "ARMED — unexpected in unrestricted mode",
                hint="a successful disable clears this. "
                     "'ark abort' restores the snapshot.")

    # ── Network enforcement ──
    health = netmgr.health.check_all(current_mode)
    if restricted:
        add("network", "pass" if health["dnsmasq_alive"] else "fail",
            "DNS through dnsmasq allowlist",
            "dnsmasq listening on 127.0.0.1:53" if health["dnsmasq_alive"]
            else "dnsmasq not reachable",
            hint=None if health["dnsmasq_alive"] else "run: systemctl restart dnsmasq")
    else:
        add("network", "info", "DNS through dnsmasq allowlist",
            "dnsmasq listening on 127.0.0.1:53" if health["dnsmasq_alive"]
            else "dnsmasq not reachable")

    rule_count = netmgr.health.nft_rule_count()
    count_desc = f"({rule_count} rules)" if rule_count >= 0 else "(count unknown)"
    if restricted:
        add("network", "pass" if health["nftables_rules"] else "fail",
            "Direct DNS blocked", count_desc,
            hint=None if health["nftables_rules"] else "run: ark enable")
    else:
        add("network", "pass" if health["nftables_rules"] else "fail",
            "No DNS drop rules", count_desc)

    if current_mode == "locked":
        add("network", "pass" if health["dns_leak"] else "fail",
            "DNS blocked as user",
            "blocked (expected)" if health["dns_leak"] else "RESOLVES — leak",
            hint=None if health["dns_leak"] else "user DNS escaping allowlist")
    else:
        add("network", "pass" if health["dns_leak"] else "fail",
            "Approved domains resolve",
            f"{netmgr.health.DNS_TEST_DOMAIN} OK" if health["dns_leak"]
            else f"{netmgr.health.DNS_TEST_DOMAIN} does not resolve",
            hint=None if health["dns_leak"] else "user DNS broken",
            detail=[f"probe: getent hosts {netmgr.health.DNS_TEST_DOMAIN} as {user}"])

    if restricted:
        add("network", "pass" if health["resolv_conf"] else "fail",
            "resolv.conf → dnsmasq",
            "nameserver 127.0.0.1" if health["resolv_conf"] else "mismatch",
            hint=None if health["resolv_conf"] else "/etc/resolv.conf not on dnsmasq")
    else:
        add("network", "info", "resolv.conf → dnsmasq",
            "nameserver 127.0.0.1" if health["resolv_conf"] else "mismatch")

    if restricted:
        add("network", "pass" if health["root_dns"] else "fail",
            "Root DNS check",
            "root can resolve" if health["root_dns"] else "fails",
            hint=None if health["root_dns"]
            else f"getent hosts {netmgr.health.DNS_TEST_DOMAIN} as root failed")
    else:
        add("network", "info", "Root DNS check",
            "root can resolve" if health["root_dns"] else "fails")

    # ── Internet namespace ──
    ns_name = netmgr.namespace.NETNS_NAME
    svc_up = _svc_active(ns_name)
    if restricted:
        add("namespace", "pass" if svc_up else "fail",
            "Internet namespace service", f"{ns_name} running" if svc_up
            else f"{ns_name} stopped",
            hint=None if svc_up else f"run: systemctl start {ns_name}")
    else:
        add("namespace", "info", "Internet namespace service",
            f"{ns_name} running" if svc_up else f"{ns_name} stopped")

    try:
        ns = netmgr.namespace.status()
    except Exception as e:  # noqa: BLE001 - NetlinkError etc, report, don't crash
        ns_ok = False
        ns_detail = None
        ns_hint = str(e)
    else:
        ns_ok = ns["namespace"] and ns["veth"] and ns["routing"]
        ns_detail = [f"namespace={ns['namespace']} veth={ns['veth']} "
                     f"routing={ns['routing']}"]
        ns_hint = None
    if restricted:
        add("namespace", "pass" if ns_ok else "fail",
            "Netns / veth / routing",
            "OK" if ns_ok else "degraded",
            hint=ns_hint, detail=ns_detail)
    else:
        add("namespace", "info", "Netns / veth / routing",
            "OK" if ns_ok else "degraded",
            hint=ns_hint, detail=ns_detail)

    grants = netmgr.wrappers.read_grants()
    if restricted:
        grant_lines = [f"{g.path} → {g.realpath}"
                       + ("  [MISSING]" if g.missing else "")
                       for g in grants]
        add("namespace", "info", "Apps available",
            f"{len(grants)} apps" if grants else "none",
            detail=grant_lines)
    else:
        add("namespace", "info", "Apps granted",
            f"{len(grants)} apps granted: {', '.join(g.path for g in grants)}"
            if grants else "no apps granted")

    # ── Timelock ──
    timelock_json: dict[str, object] = {}
    timelock_rows: list[StatusRow] = []

    def timelock_row(state: str, label: str, hint: str | None,
                     detail: list[str] | None) -> None:
        timelock_rows.append(_status_row(state, label, "", hint=hint,
                                         detail=detail))
        summary[state] += 1

    meta = lib._load_cask_metadata()
    if meta is None:
        if restricted:
            timelock_row("fail", "Timelock — no cask metadata",
                         "run: ark enable", None)
        else:
            timelock_row("info", "Timelock — no active timelock", None, None)
        timelock_json = {"active": False, "duration": None, "expires_utc": None,
                         "remaining_secs": None,
                         "state": "fail" if restricted else "info"}
    else:
        try:
            duration_secs = lib.parse_duration(meta["tle_duration"])
        except ValueError:
            timelock_row("fail", "Timelock — corrupt cask metadata",
                         f"invalid tle_duration {meta['tle_duration']!r}", None)
            timelock_json = {"active": False, "duration": meta["tle_duration"],
                             "expires_utc": None, "remaining_secs": None,
                             "state": "fail",
                             "hint": "corrupt cask metadata"}
        else:
            unlock_ts = meta["cask_timestamp"] + duration_secs
            remaining = unlock_ts - now
            active = remaining > 0
            duration_str = lib.format_duration(duration_secs)
            raw_detail = ([f"cask_timestamp={meta['cask_timestamp']} "
                           f"tle_duration={meta['tle_duration']}"]
                          if args.verbose else None)
            if restricted:
                state = "pass" if active else "warn"
                hint = None if active else (
                    "timelock expired but mode still restricted — run: ark disable")
                timelock_row(
                    state, f"{duration_str} selected at enable", hint,
                    raw_detail)
                if active:
                    timelock_row(
                        state,
                        f"Unlocks in {_format_remaining(remaining)} — "
                        f"{time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime(unlock_ts))}",
                        hint, raw_detail)
                else:
                    timelock_row(state, "Timelock expired", hint, raw_detail)
                timelock_json = {
                    "duration": meta["tle_duration"],
                    "expires_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                                 time.gmtime(unlock_ts)),
                    "remaining_secs": max(remaining, 0),
                    "active": active, "state": state, "hint": hint,
                }
            else:
                timelock_row(
                    "info", "Timelock — no active timelock (stale metadata "
                    "from the last enable)", None, raw_detail)
                timelock_json = {"active": False, "duration": meta["tle_duration"],
                                 "expires_utc": None, "remaining_secs": None,
                                 "state": "info"}

    # ── Browser policies ──
    policies = (
        ("Brave", netmgr.policies.BRAVE_POLICY),
        ("Chromium", netmgr.policies.CHROMIUM_POLICY),
        ("Chrome", netmgr.policies.CHROME_POLICY),
        ("Firefox", netmgr.policies.FIREFOX_POLICY),
    )
    valid, broken = [], []
    for name, path in policies:
        try:
            json.loads(Path(path).read_text())
            valid.append(name)
        except (OSError, json.JSONDecodeError):
            broken.append(name)
    if restricted:
        add("browser_policies", "pass" if not broken else "fail",
            "Browser policies",
            "Brave / Chromium / Chrome / Firefox valid" if not broken
            else f"{len(valid)}/4 valid — missing: {', '.join(broken)}",
            hint=None if not broken else "run: ark enable")
    else:
        add("browser_policies", "info", "Browser policies",
            "valid" if valid else "none deployed")

    transition = _last_transition()
    if args.json:
        _render_status_json(sections, timelock_json, transition, summary,
                            checked_at=checked_at, mode=current_mode)
    else:
        _render_status_text(sections, timelock_rows, transition, summary,
                            checked_at=checked_at,
                            color=sys.stdout.isatty() and not args.plain,
                            verbose=args.verbose)
    return 1 if summary["fail"] else 0


def _render_status_text(
    sections: dict[str, list[StatusRow]],
    timelock_rows: list[StatusRow],
    transition: tuple[str, str] | None,
    summary: dict[str, int], *,
    checked_at: str, color: bool, verbose: bool,
) -> None:
    total = sum(summary.values())
    need = summary["fail"] + summary["warn"]
    print(f"Ark Status · checked {checked_at}")
    print()
    for key, title in _STATUS_SECTIONS:
        if key == "timelock":
            rows = timelock_rows
        else:
            rows = sections[key]
        if not rows:
            continue
        print(title)
        for row in rows:
            state = row["state"]
            glyph = _TIMELOCK_GLYPH if key == "timelock" else _GLYPH[state]
            line = f"  {glyph} {row['label']}"
            if row["value"]:
                line += f" — {row['value']}"
            if color:
                print(f"{_ANSI[state]}{line}{_RESET}")
            else:
                print(line)
            details = row.get("detail")
            if details:
                for detail in details:
                    print(f"      {detail}")
            hint = row.get("hint")
            if hint:
                print(f"      → {hint}")
        print()
    if transition is not None:
        print(f"Last transition: {transition[0]} → {transition[1]}")
        print()
    if total == 0:
        print("No checks.")
    elif need == 0:
        print(f"All systems nominal — {total} checks, 0 need attention.")
    else:
        print(f"{total} checks, {need} need attention.")


def _render_status_json(
    sections: dict[str, list[StatusRow]],
    timelock_json: dict[str, object],
    transition: tuple[str, str] | None,
    summary: dict[str, int], *,
    checked_at: str, mode: str,
) -> None:
    def rows(name: str) -> list[dict[str, object]]:
        out: list[dict[str, object]] = []
        for row in sections[name]:
            entry: dict[str, object] = {
                "label": row["label"],
                "value": row["value"],
                "ok": {"pass": True, "warn": None, "fail": False,
                       "info": None}[row["state"]],
                "hint": row.get("hint"),
            }
            if row.get("detail"):
                entry["detail"] = row["detail"]
            out.append(entry)
        return out

    ok = None if summary["warn"] or summary["info"] else summary["fail"] == 0
    if summary["fail"]:
        ok = False
    payload: dict[str, object] = {
        "checked_at": checked_at,
        "mode": mode,
        "sections": {
            "mode_and_access": rows("mode_and_access"),
            "network": rows("network"),
            "namespace": rows("namespace"),
            "timelock": timelock_json,
            "browser_policies": rows("browser_policies"),
        },
        "last_transition": {"command": transition[0], "status": transition[1]}
        if transition else None,
        "summary": summary,
        "ok": ok,
    }
    print(json.dumps(payload, indent=2))
