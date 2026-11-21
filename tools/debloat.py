#!/usr/bin/env python3
"""debloat — pre-lockdown ADB debloat runner (Phase D0).

Mutating CLI; needs adb + a connected device. Manifest is the only allowlist:
etc/mdm/policy/debloat.json. Dry-run default; `apply` is the only mutating
subcommand and requires --yes or an interactive [y/N].

Usage:
    debloat capture [--json]
    debloat check
    debloat plan [--batch N]
    debloat apply --batch N [--yes]
    debloat verify --batch N [--json]
    debloat promote --batch N --confirm-clean-reboot [--yes]
    debloat rollback (--ts TS | --last) [--yes]

Exit codes: 0 ok, 1 failure, 2 usage/root.
Stdlib only.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

CRITICAL = {
    "com.android.systemui",
    "com.android.settings",
    "com.android.providers.settings",
    "com.android.packageinstaller",
    "com.google.android.packageinstaller",
    "com.android.permissioncontroller",
    "com.google.android.permissioncontroller",
    "com.android.phone",
    "com.android.server.telecom",
    "com.android.providers.telephony",
    "com.android.bluetooth",
    "com.android.nfc",
    "com.android.keychain",
    "com.android.shell",
    "com.android.externalstorage",
    "com.android.mtp",
    "com.android.managedprovisioning",
    "com.android.emergency",
    "com.mediatek.ims",
    "com.google.android.ims",
    "com.google.android.inputmethod.latin",
    "com.sh.smart.caller",
    "com.google.android.apps.messaging",
    "com.hmdm.launcher",
}

ROLE_REFUSE = {"HOME", "IME", "DIALER", "SMS", "EMERGENCY"}
ROLE_OVERRIDE_OK = {"SYSTEM_GALLERY", "BROWSER", "ASSISTANT"}


def get_repo_root() -> Path:
    deployed = Path.home() / ".config" / "linux_setup" / "config.txt"
    if deployed.is_file():
        for line in deployed.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if s.startswith("REPO_ROOT="):
                _, _, val = s.partition("=")
                val = val.strip().strip("'\"")
                if val:
                    return Path(val)
    return Path(__file__).resolve().parent.parent


def _adb(*args, serial=None, timeout=60):
    cmd = ["adb"]
    if serial:
        cmd += ["-s", serial]
    return subprocess.run(
        [*cmd, *args], capture_output=True, text=True, check=False, timeout=timeout
    )


def _adb_devices():
    r = _adb("devices")
    rows = []
    for line in r.stdout.splitlines()[1:]:
        line = line.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) >= 2:
            rows.append((parts[0], parts[1]))
    return rows


def _one_device():
    rows = _adb_devices()
    if not rows:
        raise RuntimeError("no adb device found")
    if len(rows) > 1:
        raise RuntimeError(f"multiple adb devices: {rows}")
    serial, state = rows[0]
    if state not in ("device",):
        raise RuntimeError(f"device state is {state!r} (expected 'device')")
    return serial


def _shell(cmd_str, serial=None, timeout=60):
    r = _adb("shell", cmd_str, serial=serial, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError(f"adb shell failed (rc={r.returncode}): {r.stderr.strip()}")
    return r.stdout


def _confirm(prompt):
    try:
        return input(f"{prompt} [y/N]: ").strip().lower() in ("y", "yes")
    except EOFError:
        return False


def load_manifest(path):
    with open(path, encoding="utf-8") as f:
        m = json.load(f)
    if m.get("schema") != 1:
        raise ValueError("manifest schema must be 1")
    if not isinstance(m.get("entries"), list):
        raise ValueError("manifest missing entries list")
    return m


def installed_set(serial=None):
    out = _shell("pm list packages", serial=serial)
    return {
        line.split(":", 1)[1].strip()
        for line in out.splitlines()
        if line.startswith("package:")
    }


def disabled_set(serial=None):
    out = _shell("pm list packages -d", serial=serial)
    return {
        line.split(":", 1)[1].strip()
        for line in out.splitlines()
        if line.startswith("package:")
    }


def all_known_set(serial=None):
    out = _shell("pm list packages -u", serial=serial)
    return {
        line.split(":", 1)[1].strip()
        for line in out.splitlines()
        if line.startswith("package:")
    }


def fingerprint(serial=None):
    return _shell("getprop ro.build.fingerprint", serial=serial).strip()


def parse_roles(serial=None):
    out = _shell("dumpsys role", serial=serial)
    roles = {}
    # format: "name=android.app.role.X" then (optionally) "holders=Y" on the
    # following lines. A role with no holder must not steal the next role's
    # holder, so match per-block with a lookahead boundary.
    import re

    for m in re.finditer(
        r"name=android\.app\.role\.(\w+)(.*?)(?=name=android\.app\.role\.|\Z)",
        out,
        re.S,
    ):
        name = m.group(1)
        block = m.group(2)
        hm = re.search(r"holders=(\S+)", block)
        roles[name] = hm.group(1).strip() if hm else ""
    return roles


def parse_ime(serial=None):
    out = _shell("ime list -s", serial=serial)
    return [line.strip() for line in out.splitlines() if line.strip()]


def device_admin_set(serial=None):
    out = _shell("dumpsys device_policy", serial=serial)
    import re

    return {m.group(1) for m in re.finditer(r"admin=ComponentInfo\{([^/]+)/", out)}


def _looks_like_remote_error(text: str) -> bool:
    for line in text.splitlines():
        s = line.strip()
        if (
            s.startswith("Error:")
            or s.startswith("Exception occurred")
            or "unknown command" in s
        ):
            return True
    return False


def cmd_capture(args):
    serial = _one_device()
    fp = fingerprint(serial)
    inst = installed_set(serial)
    dis = disabled_set(serial)
    roles = parse_roles(serial)
    ime = parse_ime(serial)
    tp = _shell("pm list packages -3", serial=serial)
    third = [
        line.split(":", 1)[1].strip()
        for line in tp.splitlines()
        if line.startswith("package:")
    ]
    snap = {
        "fingerprint": fp,
        "installed_count": len(inst),
        "disabled_count": len(dis),
        "third_party": third,
        "roles": roles,
        "ime": ime,
    }
    if args.json:
        print(json.dumps(snap, indent=2))
    else:
        print(f"fingerprint: {fp}")
        print(
            f"installed: {len(inst)}  disabled: {len(dis)}  third-party: {len(third)}"
        )
        print(f"roles: {roles}")
        print(f"ime: {ime}")
        print("third-party:")
        for t in third:
            print("  " + t)
    return 0


def validate_manifest(manifest, serial=None):
    errors = []
    warnings = []
    fp_expected = manifest.get("fingerprint")
    if serial is None:
        serial = _one_device()
    fp_live = fingerprint(serial)
    if fp_live != fp_expected:
        errors.append(f"fingerprint mismatch: live={fp_live} expected={fp_expected}")

    entries = manifest["entries"]
    pkgs = [e["pkg"] for e in entries]
    if len(pkgs) != len(set(pkgs)):
        dupes = [p for p in set(pkgs) if pkgs.count(p) > 1]
        errors.append(f"duplicate packages: {dupes}")

    known = all_known_set(serial) if serial else set()
    roles = parse_roles(serial) if serial else {}
    ime_list = parse_ime(serial) if serial else []
    if serial and not roles:
        errors.append("dumpsys role parse produced no roles — verify parser")

    # critical set intersection
    crit_hit = [
        e["pkg"] for e in entries if e["pkg"] in CRITICAL and e["tier"] in (1, 2)
    ]
    if crit_hit:
        errors.append(f"critical-set intersection: {crit_hit}")

    # runtime device-admin intersection (spec: any device admin refused)
    admins = device_admin_set(serial) if serial else set()
    admin_hit = [
        e["pkg"] for e in entries if e["tier"] in (1, 2) and e["pkg"] in admins
    ]
    if admin_hit:
        errors.append(f"device-admin intersection: {admin_hit}")

    # tier/action/batch consistency
    for e in entries:
        t = e.get("tier")
        a = e.get("action")
        b = e.get("batch")
        if t == 1 and a != "uninstall-user0":
            errors.append("{}: tier1 must be uninstall-user0".format(e["pkg"]))
        if t == 2 and a != "disable-first":
            errors.append("{}: tier2 must be disable-first".format(e["pkg"]))
        if t == 3 and a != "keep":
            errors.append("{}: tier3 must be keep".format(e["pkg"]))
        if t in (1, 2):
            if b is None or not (0 <= b <= 7):
                errors.append("{}: tier1/2 needs batch 0-7".format(e["pkg"]))
            if e.get("checked") is None:
                errors.append("{}: tier1/2 needs checked".format(e["pkg"]))
        else:
            if b is not None:
                errors.append("{}: tier3 must have batch null".format(e["pkg"]))
        if e["pkg"] in CRITICAL and t in (1, 2):
            errors.append("{}: critical package in tier {}".format(e["pkg"], t))
        # role refusal
        ro = e.get("role_override")
        if ro is not None and ro not in ROLE_OVERRIDE_OK:
            errors.append("{}: invalid role_override {}".format(e["pkg"], ro))
        # presence check — Tier 2 only as a hard error: disable-first requires
        # the package to exist. Tier 1's goal state is absence, so an absent
        # Tier 1 is never an error here (lifecycle-aware; see runbook).
        if e.get("tier") == 2 and known and e["pkg"] not in known:
            errors.append(
                "{}: not present on device (pm list packages -u)".format(e["pkg"])
            )
        # Tier 1 absence is advisory — may be already removed (this cycle) or
        # never present on this ROM variant. apply skips it gracefully.
        if e.get("tier") == 1 and known and e["pkg"] not in known:
            warnings.append(
                "{}: not present on device (already removed or absent from ROM)".format(
                    e["pkg"]
                )
            )

    # role holders: refuse if a tier1/2 entry is a role holder without override
    holder_pkgs = set(roles.values())
    for e in entries:
        if e["tier"] in (1, 2) and e["pkg"] in holder_pkgs:
            ro = e.get("role_override")
            role_name = None
            for rname, holder in roles.items():
                if holder == e["pkg"]:
                    role_name = rname
            if role_name in ROLE_REFUSE:
                errors.append("{}: holds refused role {}".format(e["pkg"], role_name))
            elif role_name in ROLE_OVERRIDE_OK and ro != role_name:
                errors.append(
                    "{}: holds role {} but role_override={}".format(
                        e["pkg"], role_name, ro
                    )
                )

    # Chrome alone in batch 7
    batch7 = [e["pkg"] for e in entries if e.get("batch") == 7]
    if batch7 != ["com.android.chrome"]:
        errors.append(f"batch 7 must be exactly [com.android.chrome]: {batch7}")

    # no batch > 7
    for e in entries:
        b = e.get("batch")
        if b is not None and (b < 0 or b > 7):
            errors.append("{}: batch out of range: {}".format(e["pkg"], b))

    # ime single
    if len(ime_list) != 1:
        errors.append(f"expected single IME, got {ime_list}")

    return errors, warnings, serial


def cmd_check(args):
    manifest = load_manifest(args.manifest)
    serial = _one_device()
    errors, warnings, _ = validate_manifest(manifest, serial=serial)
    for w in warnings:
        print(f"WARN: {w}")
    if errors:
        for e in errors:
            print(f"FAIL: {e}")
        print(f"check: {len(errors)} errors")
        return 1
    n = len(manifest["entries"])
    t1 = sum(1 for e in manifest["entries"] if e["tier"] == 1)
    t2 = sum(1 for e in manifest["entries"] if e["tier"] == 2)
    t3 = sum(1 for e in manifest["entries"] if e["tier"] == 3)
    print(
        f"check: OK — {n} entries ({t1} tier1, {t2} tier2, {t3} tier3)"
        + (f" ({len(warnings)} warnings)" if warnings else "")
    )
    print(f"fingerprint: {fingerprint(serial)}")
    return 0


def cmd_plan(args):
    manifest = load_manifest(args.manifest)
    serial = _one_device()
    errors, warnings, _ = validate_manifest(manifest, serial=serial)
    for w in warnings:
        print(f"WARN: {w}", file=sys.stderr)
    if errors:
        for e in errors:
            print(f"FAIL: {e}")
        return 1
    batch_filter = args.batch
    for e in manifest["entries"]:
        if e["tier"] not in (1, 2):
            continue
        if batch_filter is not None and e["batch"] != batch_filter:
            continue
        print("[batch {}] {} {}".format(e["batch"], e["action"], e["pkg"]))
    return 0


def _state_dir(args):
    if args.state_dir:
        return Path(args.state_dir).expanduser()
    return Path.home() / ".local" / "state" / "mdm" / "debloat"


def _snapshot(serial, path):
    inst = installed_set(serial)
    dis = disabled_set(serial)
    known = all_known_set(serial)
    lines = []
    for pkg in sorted(known):
        is_inst = "1" if pkg in inst else "0"
        is_dis = "1" if pkg in dis else "0"
        # path via pm path (best effort)
        r = _adb("shell", f"pm path {pkg}", serial=serial)
        pth = ""
        for line in r.stdout.splitlines():
            if line.startswith("package:"):
                pth = line.split(":", 1)[1].strip()
        lines.append(f"{pkg}\t{is_inst}\t{is_dis}\t{pth}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def cmd_apply(args):
    if args.batch is None:
        print("apply requires --batch N", file=sys.stderr)
        return 2
    manifest = load_manifest(args.manifest)
    serial = _one_device()
    errors, _warnings, _ = validate_manifest(manifest, serial=serial)
    if errors:
        for e in errors:
            print(f"FAIL: {e}", file=sys.stderr)
        return 1

    batch_entries = [
        e
        for e in manifest["entries"]
        if e.get("batch") == args.batch and e["tier"] in (1, 2)
    ]
    if not batch_entries:
        print(f"no entries for batch {args.batch}", file=sys.stderr)
        return 1

    state = _state_dir(args)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    snap_dir = state / ts
    snap_dir.mkdir(parents=True, exist_ok=True)

    # snapshot before
    _snapshot(serial, snap_dir / "before.tsv")

    if not args.yes:
        print(f"Batch {args.batch}: {len(batch_entries)} entries")
        for e in batch_entries:
            print("  {} {}".format(e["action"], e["pkg"]))
        if not _confirm(f"Apply batch {args.batch}?"):
            print("aborted")
            return 1

    applied = []
    failed = None
    known = all_known_set(serial)
    for e in batch_entries:
        pkg = e["pkg"]
        # idempotent: skip entries already absent (already removed or never present).
        # Skips are recorded as "skip" and excluded from restore.sh (nothing to undo).
        if pkg not in known:
            applied.append((pkg, e["action"], "skip", "not present on device"))
            print(f"skip: {e['action']} {pkg}")
            continue
        if e["action"] == "uninstall-user0":
            r = _adb(
                "shell", f"pm uninstall --user 0 {pkg}", serial=serial, timeout=120
            )
        elif e["action"] == "disable-first":
            r = _adb(
                "shell", f"pm disable-user --user 0 {pkg}", serial=serial, timeout=120
            )
        else:
            r = None
        combined = (r.stdout or "") + (r.stderr or "") if r is not None else ""
        remote_err = _looks_like_remote_error(combined)
        if e["action"] == "uninstall-user0":
            ok = (
                r is not None
                and r.returncode == 0
                and "Success" in combined
                and not remote_err
            )
        elif e["action"] == "disable-first":
            ok = r is not None and r.returncode == 0 and not remote_err
        else:
            ok = False
        applied.append((pkg, e["action"], "ok" if ok else "FAIL", combined.strip()))
        if not ok:
            failed = pkg
            break

    # write applied.tsv
    with open(snap_dir / "applied.tsv", "w", encoding="utf-8") as f:
        for pkg, action, status, msg in applied:
            f.write(
                "{}\t{}\t{}\t{}\n".format(pkg, action, status, msg.replace("\n", " "))
            )

    # restore.sh
    restore_lines = [
        "#!/usr/bin/env bash",
        "set -e",
        "SERIAL=${1:-}",
        'ADB="adb"',
        'if [ -n "$SERIAL" ]; then ADB="adb -s $SERIAL"; fi',
    ]
    for pkg, action, status, _msg in applied:
        if status != "ok":
            continue
        if action == "uninstall-user0":
            restore_lines.append(
                f'$ADB shell "cmd package install-existing --user 0 {pkg}"'
            )
        elif action == "disable-first":
            restore_lines.append(f'$ADB shell "pm enable {pkg}"')
    restore_lines.append('echo "rollback done"')
    (snap_dir / "restore.sh").write_text(
        "\n".join(restore_lines) + "\n", encoding="utf-8"
    )
    import os

    os.chmod(snap_dir / "restore.sh", 0o755)

    for pkg, action, status, msg in applied:
        print(f"{status}: {action} {pkg}")
        if msg and status != "ok":
            print("   " + msg)

    if failed:
        print(
            f"FAILED on {failed} — stopped. Rollback: bash {snap_dir}/restore.sh",
            file=sys.stderr,
        )
        return 1
    print(f"snapshot: {snap_dir}")
    print(f"rollback: bash {snap_dir}/restore.sh")
    return 0


def cmd_verify(args):
    manifest = load_manifest(args.manifest)
    serial = _one_device()
    batch = args.batch
    inst = installed_set(serial)
    dis = disabled_set(serial)
    roles = parse_roles(serial)

    failures = []
    checked = [e for e in manifest["entries"] if e["tier"] in (1, 2)]
    if batch is not None:
        checked = [e for e in checked if e.get("batch") == batch]

    for e in checked:
        if e["tier"] == 1:
            if e["pkg"] in inst:
                failures.append("tier1 still installed: {}".format(e["pkg"]))
        elif e["tier"] == 2:
            if e["pkg"] not in inst:
                failures.append("tier2 missing: {}".format(e["pkg"]))
            elif e["pkg"] not in dis:
                failures.append("tier2 not disabled: {}".format(e["pkg"]))

    # Refused roles must still be held; override roles may become unheld.
    ime_list = parse_ime(serial)
    if len(ime_list) != 1:
        failures.append(f"expected single IME, got {ime_list}")
    for r in ("HOME", "DIALER", "SMS", "EMERGENCY"):
        if r not in roles or not roles.get(r):
            failures.append(f"refused role unheld: {r}")
        elif roles[r] not in inst:
            failures.append(f"refused role {r} holder absent: {roles[r]}")

    if failures:
        for f in failures:
            print(f"FAIL: {f}")
        if args.json:
            print(json.dumps({"ok": False, "failures": failures}, indent=2))
        return 1
    print(f"verify: OK ({len(checked)} entries checked)")
    if args.json:
        print(json.dumps({"ok": True, "checked": len(checked)}, indent=2))
    return 0


def cmd_promote(args):
    if not args.confirm_clean_reboot:
        print("promote requires --confirm-clean-reboot", file=sys.stderr)
        return 2
    manifest = load_manifest(args.manifest)
    serial = _one_device()
    state = _state_dir(args)
    # find latest snapshot
    snaps = sorted(state.iterdir()) if state.exists() else []
    if not snaps:
        print("no snapshots found", file=sys.stderr)
        return 1
    snap_dir = snaps[-1]
    if args.ts:
        snap_dir = state / args.ts
    if not snap_dir.exists():
        print(f"snapshot not found: {snap_dir}", file=sys.stderr)
        return 1

    batch = args.batch
    tier2_entries = [
        e for e in manifest["entries"] if e["tier"] == 2 and e.get("batch") == batch
    ]
    if not tier2_entries:
        print(f"no tier2 entries for batch {batch}", file=sys.stderr)
        return 1

    if not args.yes:
        print(
            f"Promote {len(tier2_entries)} tier-2 entries from batch {batch} to uninstall"
        )
        if not _confirm("Proceed?"):
            print("aborted")
            return 1

    dis = disabled_set(serial)
    failed = None
    for e in tier2_entries:
        pkg = e["pkg"]
        if pkg not in dis:
            print(f"SKIP (not disabled): {pkg}")
            continue
        r = _adb("shell", f"pm uninstall --user 0 {pkg}", serial=serial, timeout=120)
        ok = r.returncode == 0
        print("{}: uninstall {}".format("ok" if ok else "FAIL", pkg))
        if not ok:
            failed = pkg
            break
    if failed:
        return 1
    # record promoted
    with open(snap_dir / "promoted.txt", "a", encoding="utf-8") as f:
        f.write(f"batch {batch} promoted {datetime.now(timezone.utc).isoformat()}\n")
    return 0


def cmd_rollback(args):
    state = _state_dir(args)
    if args.last:
        snaps = sorted(state.iterdir()) if state.exists() else []
        if not snaps:
            print("no snapshots", file=sys.stderr)
            return 1
        snap_dir = snaps[-1]
    elif args.ts:
        snap_dir = state / args.ts
    else:
        print("rollback requires --ts or --last", file=sys.stderr)
        return 2
    if not snap_dir.exists():
        print(f"snapshot not found: {snap_dir}", file=sys.stderr)
        return 1
    serial = _one_device()
    fp_live = fingerprint(serial)
    manifest = load_manifest(args.manifest)
    if fp_live != manifest.get("fingerprint"):
        print("fingerprint mismatch — refusing rollback", file=sys.stderr)
        return 1
    restore = snap_dir / "restore.sh"
    if not restore.exists():
        print("restore.sh missing", file=sys.stderr)
        return 1
    if not args.yes:
        print(f"Rollback using {restore}")
        if not _confirm("Proceed?"):
            print("aborted")
            return 1
    r = subprocess.run(["bash", str(restore), serial], capture_output=True, text=True)
    print(r.stdout)
    if r.returncode != 0:
        print(r.stderr, file=sys.stderr)
        return 1
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="debloat", description="pre-lockdown ADB debloat runner"
    )
    ap.add_argument(
        "--manifest",
        default=str(get_repo_root() / "etc" / "mdm" / "policy" / "debloat.json"),
    )
    ap.add_argument("--state-dir", default=None)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("capture")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_capture)

    p = sub.add_parser("check")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("plan")
    p.add_argument("--batch", type=int, default=None)
    p.set_defaults(func=cmd_plan)

    p = sub.add_parser("apply")
    p.add_argument("--batch", type=int, required=True)
    p.add_argument("--yes", action="store_true")
    p.set_defaults(func=cmd_apply)

    p = sub.add_parser("verify")
    p.add_argument("--batch", type=int, default=None)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("promote")
    p.add_argument("--batch", type=int, required=True)
    p.add_argument("--confirm-clean-reboot", action="store_true")
    p.add_argument("--yes", action="store_true")
    p.set_defaults(func=cmd_promote)

    p = sub.add_parser("rollback")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--ts", default=None)
    g.add_argument("--last", action="store_true")
    p.add_argument("--yes", action="store_true")
    p.set_defaults(func=cmd_rollback)

    args = ap.parse_args(argv)
    try:
        return args.func(args)
    except (RuntimeError, ValueError, OSError, json.JSONDecodeError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
