#!/usr/bin/env python3
"""Standalone post-install.py restore (Phase 40-D D1, restore-only).

Intentional isolated operation — never trunk-wired (isolation per
docs/adr/012-restore-standalone-post-install.md). Run only after
./install.py has converged, the reboot is taken, and the system
verified stable. The lib converges nothing itself.

Two-phase new-machine rule:
    ./install.py (restore-free) -> reboot -> verify-stable
    -> python3 lib/40-restore.py

Order: preconditions -> preflight 10/11/12 -> blank-iron gate
(every target absent-or-empty, else fail-stop + conflict list) ->
best-effort pre-restore-<ts> safety tag (warn-and-continue) ->
staged restore (/tmp/restore-<ts> --verify -> preview incoming list ->
atomic per-path os.replace parents-first, 600/700 + chown hardened
before swap, no --delete at all) -> done.

Password chain owned by the lib (no trunk path exists):
--restore-password-file, then --password-command (or the ambient
RESTIC_PASSWORD_COMMAND), then TTY prompt via /dev/tty (helpers live
in restic_common; Bitwarden is the authoritative source). Empty input
warns of the impact and offers one retype; declining (or a second
empty) proceeds with an empty value, loudly warned. Headless without
a file/command fails naming it.

Exit codes (0/1 convention):
    0  restored (or preview-only OK; the trailing RESULT status is
       authoritative for the trunk, the exit code is for standalone use)
    1  failure (precondition, preflight, gate conflict, restore error,
       usage error)

Result trailer (decision 2):
    Every terminal outcome prints `RESULT {json}` with status/changed/
    message. install.py's parse_result_trailer prefers it over the exit
    code, so the trunk summary reports the real reason instead of a bare
    rc. Emit it on stdout (the trailer is machine-readable; all human
    diagnostics already go to stderr), never on a partial outcome.

Stdlib only.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "lib" / "helpers"))

from preconditions import require_user  # noqa: E402
from restic_common import (  # noqa: E402
    base_args,
    build_repo_env,
    classify_stderr,
    preflight,
    resolve_b2_credentials,
    resolve_password,
    restic_binary,
    stat_excludes,
)

ASSUMPTION_BANNER = (
    "40-restore: assumption: ./install.py has converged this system, "
    "the reboot is taken, and the system verified stable — "
    "this lib converges nothing itself."
)

# Livedata restore targets (home-relative). The Pi agent was removed, so
# nothing under ~/.pi/ is backed up or restored. Wholesale ~/.config and
# ~/.local/share are never restored. GPG/gopass were removed from the
# repo (gopass-removal postmortem), so ~/.gnupg is no longer a target.
# "knowledge_base" is the notes-vault literal (backup-owned path; never
# derived from OBSIDIAN_VAULT_PATH — var retained as deploy pointer per
# the 12-D amendment, still never read here).
LIVEDATA_TARGETS = (
    "knowledge_base",
    "Videos/keep",
    ".ssh",
)


def log(msg: str) -> None:
    print(f"40-restore: {msg}", file=sys.stderr)


def warn(msg: str) -> None:
    print(f"40-restore: WARN {msg}", file=sys.stderr)


def emit_result(status: str, changed: bool, message: str) -> None:
    """Print the RESULT trailer install.py's parse_result_trailer reads.

    stdout on purpose: this line is machine-readable and the trunk merges
    it with stderr anyway, while the standalone operator gets the human
    diagnostics on stderr. `message` is a reason, never a secret.
    """
    print("RESULT " + json.dumps({"status": status, "changed": changed, "message": message}))


def fail(msg: str) -> int:
    """Terminal failure: stderr for the human, RESULT for the trunk.

    Every nonzero return in this module funnels through here (staged_restore
    and main alike), so emitting here is what guarantees the trailer exists
    on every failure — see the exit-code contract above.
    """
    print(f"40-restore: ERROR {msg}", file=sys.stderr)
    emit_result("FAIL", False, msg)
    return 1


def real_home() -> Path:
    home = os.environ.get("HOME")
    return Path(home) if home else Path.home()


def target_paths() -> list[Path]:
    """Paths this restore owns: the blank-iron gate and the
    pre-restore-<ts> safety snapshot."""
    home = real_home()
    return [home / t for t in LIVEDATA_TARGETS]


def check_preconditions(allow_pending_reboot: bool = False) -> list[str]:
    """Verify-only, never converge. Each failure names the fix."""
    failures: list[str] = []
    if restic_binary() is None:
        failures.append("restic binary missing — re-run ./install.py (phase 40-A ships restic)")
    # Skeleton target dirs exist (never auto-created here).
    home = real_home()
    for parent in (home / "Videos",):
        try:
            if not parent.is_dir():
                failures.append(f"skeleton dir missing: {parent} — re-run ./install.py (phase 15)")
        except OSError:
            failures.append(f"skeleton dir unreadable: {parent} — re-run ./install.py (phase 15)")
    # Net alive (same probes as stage-0, minus apt).
    try:
        dns = subprocess.run(["getent", "hosts", "deb.debian.org"], capture_output=True, timeout=10).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        dns = False
    try:
        with socket.create_connection(("deb.debian.org", 443), timeout=5):
            tcp = True
    except OSError:
        tcp = False
    if not (dns and tcp):
        failures.append("network unreachable (DNS+TCP probes failed) — fix WiFi/net, then re-run")
    # Excludes file stats clean.
    try:
        stat_excludes()
    except (OSError, FileNotFoundError) as exc:
        failures.append(f"excludes file bad: {exc} — re-run ./install.py (phase 40-B)")
    # Reboot pending is fail-stop unless the operator owns the risk.
    latch = Path.home() / ".config" / "install" / ".install-need-reboot"
    if latch.is_file() or Path("/run/reboot-required").is_file():
        if allow_pending_reboot:
            warn("--allow-pending-reboot: reboot pending, operator owns the risk (logged)")
        else:
            failures.append("reboot pending (latch or /run/reboot-required) — reboot and verify-stable first")
    return failures


def blank_iron_gate(targets: list[Path]) -> list[Path]:
    """Return the conflict list (empty = gate passes). Fail-closed per target.

    absent = clean; file/symlink = conflict (symlinks never resolved);
    zero-entry dir incl. hidden = clean; unreadable = conflict.
    """
    conflicts: list[Path] = []
    for t in targets:
        try:
            if t.is_symlink():
                conflicts.append(t)
            elif not t.exists():
                continue
            elif t.is_file():
                conflicts.append(t)
            elif t.is_dir():
                try:
                    if any(t.iterdir()):
                        conflicts.append(t)
                except OSError:
                    conflicts.append(t)
            else:
                conflicts.append(t)
        except OSError:
            conflicts.append(t)
    return conflicts


def safety_snapshot(repo_env, tag: str | None) -> None:
    """Best-effort pre-restore-<ts> tag. Failure = warn-and-continue."""
    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d-%H%M%S")
    existing = [str(t) for t in target_paths() if t.exists() and not t.is_symlink()]
    if not existing:
        return  # blank iron: nothing to lose
    cmd = ["restic", *base_args(), "backup", "--tag", f"pre-restore-{ts}", *existing]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=600, env=repo_env)
        if r.returncode != 0:
            warn(f"safety snapshot failed (warn-and-continue): {(r.stderr or '').strip().splitlines() or ['rc!=0'][-1]}")
    except (OSError, subprocess.TimeoutExpired) as exc:
        warn(f"safety snapshot failed (warn-and-continue): {exc}")


def harden_tree(root: Path, uid: int, gid: int) -> None:
    """Harden staging copies BEFORE swap: dirs 700, files 600, chown user."""
    for dirpath, dirnames, filenames in os.walk(root):
        for d in dirnames:
            p = Path(dirpath) / d
            try:
                if not p.is_symlink():
                    os.chmod(p, 0o700)
                    os.chown(p, uid, gid)
            except OSError:
                pass
        for f in filenames:
            p = Path(dirpath) / f
            try:
                if not p.is_symlink():
                    os.chmod(p, 0o600)
                    os.chown(p, uid, gid)
            except OSError:
                pass
    try:
        os.chmod(root, 0o700)
        os.chown(root, uid, gid)
    except OSError:
        pass


def staged_restore(repo_env, args: argparse.Namespace) -> int:
    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d-%H%M%S")
    staging = Path(tempfile.gettempdir()) / f"restore-{ts}"
    staging.mkdir(parents=True, exist_ok=False)
    success = False
    try:
        snap = args.snapshot or "latest"
        cmd = ["restic", *base_args(), "restore", snap, "--target", str(staging), "--verify"]
        if args.tag:
            cmd += ["--tag", args.tag]
        log(f"restoring snapshot '{snap}' to staging {staging}")
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=1800, env=repo_env)
        if r.returncode != 0:
            code = classify_stderr(r.stderr or "")
            if code == 12:
                return fail("wrong password during restore — see Bitwarden vault (40-E custody)")
            tail = ((r.stderr or "").strip().splitlines() or ["restore failed"])[-1]
            return fail(f"restic restore failed: {tail}")
        # Preview = incoming file list.
        incoming = sorted(p.relative_to(staging) for p in staging.rglob("*") if not p.is_symlink() or True)
        # Snapshots store absolute source paths (restic keeps the full
        # tree), so staging holds <home-without-slash>/... — map back to
        # home-relative or fail loud (a foreign home must never land in
        # the wrong tree). Intermediate parents above home are skipped.
        home = real_home()
        prefixes = [home.parts[1:]]
        try:
            resolved = home.resolve().parts[1:]
            if resolved != prefixes[0]:
                prefixes.append(resolved)
        except OSError:
            pass
        mapped: list[tuple[Path, Path]] = []  # (staging-rel, home-rel)
        for rel in incoming:
            hit = False
            for prefix in prefixes:
                if rel.parts[: len(prefix)] == prefix:
                    if len(rel.parts) > len(prefix):
                        mapped.append((rel, Path(*rel.parts[len(prefix):])))
                    # exact home root itself: skipped, created as needed
                    hit = True
                    break
                if prefix[: len(rel.parts)] == rel.parts:
                    hit = True  # intermediate parent above home — skipped
                    break
            if not hit:
                return fail(f"unmappable snapshot path (foreign home?): {rel} — refusing to land it under {home}")
        incoming = mapped
        incoming_files = [(s, h) for s, h in incoming if (staging / s).is_file() and not (staging / s).is_symlink()]
        log(f"preview: {len(incoming_files)} incoming files (first 50):")
        for _, h in incoming_files[:50]:
            print(f"  + {h}")
        if len(incoming_files) > 50:
            log(f"... and {len(incoming_files) - 50} more")
        if args.preview_only:
            log("preview-only: staged tree kept, nothing moved in")
            success = True
            return 0
        # Atomic move-in parents-first, hardened before swap, no --delete.
        by_depth = sorted(incoming, key=lambda pair: len(pair[1].parts))
        sudo_user = os.environ.get("SUDO_USER") or os.environ.get("USER") or ""
        try:
            import pwd

            pw = pwd.getpwnam(sudo_user) if sudo_user else pwd.getpwuid(os.getuid())
            uid, gid = pw.pw_uid, pw.pw_gid
        except (KeyError, ImportError):
            uid, gid = os.getuid(), os.getgid()
        harden_tree(staging, uid, gid)
        moved = 0

        def _mkdir_dest(path: Path) -> None:
            """mkdir -p where every created level is 700 + user-owned.

            Stops at the first existing ancestor (home itself always
            exists), so pre-existing dirs above the target are untouched.
            Needed because staging hardening cannot cover destination
            parents, and under sudo fresh dirs would be root-owned 775.
            """
            if path.exists():
                return
            stack: list[Path] = []
            cur = path
            while not cur.exists():
                stack.append(cur)
                cur = cur.parent
            for p in reversed(stack):
                p.mkdir(exist_ok=True)
                try:
                    os.chmod(p, 0o700)
                    os.chown(p, uid, gid)
                except OSError:
                    pass

        for staging_rel, home_rel in by_depth:
            src = staging / staging_rel
            dst = home / home_rel
            if src.is_symlink() or src.is_file():
                _mkdir_dest(dst.parent)
                if dst.exists() or dst.is_symlink():
                    return fail(f"gate race: target appeared mid-restore: {dst}")
                os.replace(src, dst)
                moved += 1
            elif src.is_dir():
                _mkdir_dest(dst)
        log(f"moved in {moved} files (additive only, no --delete)")
        success = True
        return 0
    finally:
        if success:
            shutil.rmtree(staging, ignore_errors=True)
        else:
            log(f"staging kept for forensics: {staging}")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Standalone post-install.py restore (40-D D1)")
    p.add_argument("--restore-password-file", default="", help="path to file holding the repo password")
    p.add_argument("--password-command", default="", help="override password command (or set RESTIC_PASSWORD_COMMAND)")
    p.add_argument("--allow-pending-reboot", action="store_true", help="proceed despite reboot latch (operator owns risk)")
    p.add_argument("--snapshot", default="latest", help="snapshot ID or 'latest' (default)")
    p.add_argument("--tag", default="", help="snapshot tag selector (restic --tag filter)")
    p.add_argument("--overwrite", default="if-changed", choices=["if-changed"], help="additive move-in only")
    p.add_argument("--preview-only", action="store_true", help="stage + preview, move nothing in")
    return p


def main(argv: list[str] | None = None) -> int:
    if not require_user("lib/40-restore.py", "python3 lib/40-restore.py"):
        emit_result("FAIL", False, "refused to run as root")
        return 1
    print(ASSUMPTION_BANNER, file=sys.stderr)
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        # argparse exits 2 on bad flags, and opslog.map_rc maps 2 to SKIP --
        # so a typo'd flag would be reported to the trunk as a skip, i.e. a
        # success. Emit FAIL and retire the 2 (nothing reads it). --help's
        # SystemExit(0) is a success; re-raise so it keeps its exit code.
        if exc.code in (0, None):
            raise
        emit_result("FAIL", False, "bad arguments")
        return 1
    tag = args.tag or None

    failures = check_preconditions(allow_pending_reboot=args.allow_pending_reboot)
    if failures:
        for f in failures:
            print(f"40-restore: ERROR precondition: {f}", file=sys.stderr)
        emit_result("FAIL", False, f"{len(failures)} precondition(s) failed: {failures[0]}")
        return 1

    try:
        pw_file, pw_cmd, tmp_pw = resolve_password(
            args.restore_password_file or None,
            args.password_command or None,
            "40-restore",
            "40-restore: restic repo password (Bitwarden-held string):",
        )
    except ValueError as exc:
        return fail(str(exc))
    cleanup_tmp = tmp_pw

    repo_env = None
    try:
        try:
            b2_key_id, b2_secret = resolve_b2_credentials("40-restore")
        except ValueError as exc:
            return fail(str(exc))
        try:
            repo_env = build_repo_env(
                password_command=pw_cmd,
                password_file=pw_file,
                access_key_id=b2_key_id,
                secret_access_key=b2_secret,
            )
        except (OSError, RuntimeError) as exc:
            return fail(f"repo env: {exc}")

        code, detail = preflight(repo_env)
        if code == 10:
            print(f"40-restore: ERROR preflight 10=missing: {detail}", file=sys.stderr)
            print("  List hosts with: restic snapshots (no auto-init)", file=sys.stderr)
            emit_result("FAIL", False, f"preflight 10=missing: {detail}")
            return 1
        if code == 11:
            print(f"40-restore: ERROR preflight 11=locked: {detail}", file=sys.stderr)
            print("  After confirming no live holder: restic unlock", file=sys.stderr)
            emit_result("FAIL", False, f"preflight 11=locked: {detail}")
            return 1
        if code == 12:
            return fail(f"preflight 12=wrong-pw: {detail} — see Bitwarden vault (40-E custody)")

        targets = target_paths()
        conflicts = blank_iron_gate(targets)
        if conflicts:
            print("40-restore: ERROR blank-iron gate: populated targets refused (fail-stop):", file=sys.stderr)
            for c in conflicts:
                print(f"  conflict: {c}", file=sys.stderr)
            listed = ", ".join(str(c) for c in conflicts)
            emit_result("FAIL", False, f"blank-iron gate: populated targets refused: {listed}")
            return 1

        safety_snapshot(repo_env.env, tag)
        rc = staged_restore(repo_env.env, args)
        if rc != 0:
            return rc  # already trailered by fail() inside staged_restore
        log("done")
        if args.preview_only:
            emit_result("OK", False, f"preview-only: {tag or 'livedata'} staged, nothing moved in")
        else:
            emit_result("OK", True, f"restored {tag or 'livedata'} stream")
        return 0
    finally:
        if cleanup_tmp is not None:
            try:
                cleanup_tmp.unlink()
            except OSError:
                pass


if __name__ == "__main__":
    sys.exit(main())
