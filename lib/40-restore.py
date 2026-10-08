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
before swap, no --delete at all) -> converge ->
`pi-setup auth/probe/fetch` regenerates curated/ (never restored).

Password chain owned by the lib (no trunk path exists):
--restore-password-file, then --password-command (default the gopass
reader, covering re-restore on converged machines), then TTY prompt
<=3 attempts via /dev/tty, then paperkey hint. Headless without the
file flag fails naming it.

Exit codes (0/2/1 convention):
    0  restored (or preview-only OK)
    1  failure (precondition, preflight, gate conflict, restore error)
    2  usage error (bad flags, headless-without-file)

Stdlib only.
"""
from __future__ import annotations

import argparse
import datetime
import getpass
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
    default_password_command,
    preflight,
    restic_binary,
    stat_excludes,
)

ASSUMPTION_BANNER = (
    "40-restore: assumption: ./install.py has converged this system, "
    "the reboot is taken, and the system verified stable — "
    "this lib converges nothing itself."
)

# Livedata restore targets (home-relative). Nothing under ~/.pi/ is ever
# backed up or restored: settings reseed from dev/pi/deploy/settings.seed.json
# (only-if-absent), extensions redeploy verbatim via make dev, curated/ and
# models-store.json regenerate via pi-setup (D4 mirror rule). curated/ is
# never restored. Wholesale ~/.config and ~/.local/share are never
# restored — the vault stream names the store subtree explicitly instead.
# "knowledge_base" is the notes-vault literal (backup-owned path; never
# derived from OBSIDIAN_VAULT_PATH, which sunsets at 12-D per
# 12-unified-config.md §12.6 — config deletion must be a non-event here).
LIVEDATA_TARGETS = (
    "knowledge_base",
    "Videos/keep",
    ".ssh",
    ".gnupg",
)
# Vault-stream consumer target: the store subtree only (never wholesale
# ~/.local/share). GPG key-export import (gpg --import + ownertrust) is the
# ordered first path per 40-D1c but the producer owns the export per ADR-002 —
# TODO(V2): implement the import leg once V2 ships the export path set.
VAULT_STORE_SUBTREE = ".local/share/gopass"


def log(msg: str) -> None:
    print(f"40-restore: {msg}", file=sys.stderr)


def warn(msg: str) -> None:
    print(f"40-restore: WARN {msg}", file=sys.stderr)


def fail(msg: str) -> int:
    print(f"40-restore: ERROR {msg}", file=sys.stderr)
    return 1


def real_home() -> Path:
    home = os.environ.get("HOME")
    return Path(home) if home else Path.home()


def target_paths(tag: str | None) -> list[Path]:
    home = real_home()
    if tag == "vault":
        return [home / VAULT_STORE_SUBTREE]
    return [home / t for t in LIVEDATA_TARGETS]


def check_preconditions(args: argparse.Namespace) -> list[str]:
    """Verify-only, never converge. Each failure names the fix."""
    failures: list[str] = []
    if restic_binary() is None:
        failures.append("restic binary missing — re-run ./install.py (phase 40-A ships restic)")
    # Skeleton target dirs exist (never auto-created here).
    home = real_home()
    for parent in (home / "Videos", home / ".pi" / "agent"):
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
        if args.allow_pending_reboot:
            warn("--allow-pending-reboot: reboot pending, operator owns the risk (logged)")
        else:
            failures.append("reboot pending (latch or /run/reboot-required) — reboot and verify-stable first")
    return failures


def prompt_tty_password() -> str | None:
    """Prompt via /dev/tty (never stdin — stdin may be piped). None when no TTY."""
    try:
        with open("/dev/tty", "r+") as tty:
            tty.write("40-restore: restic repo password (Bitwarden-held string): ")
            tty.flush()
            return getpass.getpass("", stream=tty).strip() or None
    except OSError:
        return None


def resolve_password(args: argparse.Namespace) -> tuple[str | None, str | None, Path | None]:
    """Return (password_file, password_command, temp_to_cleanup).

    Chain: --restore-password-file -> --password-command -> TTY <=3 ->
    paperkey hint. Headless without the file flag fails naming it.
    """
    if args.restore_password_file:
        p = Path(args.restore_password_file).expanduser()
        if not p.is_file():
            raise ValueError(f"--restore-password-file not found: {p}")
        return str(p), None, None
    if args.password_command:
        return None, args.password_command, None
    # Try the default gopass reader once (covers re-restore on converged
    # machines where the store already exists). Failure falls through to
    # TTY — the gopass store itself arrives via this restore, so on a
    # fresh machine the operator types the Bitwarden-held string.
    default_cmd = default_password_command()
    probe = subprocess.run(
        ["bash", "-c", f"{default_cmd} >/dev/null 2>&1"],
        capture_output=True,
        timeout=30,
    )
    if probe.returncode == 0:
        return None, default_cmd, None
    if not sys.stdin.isatty() and not Path("/dev/tty").exists():
        raise ValueError("headless without --restore-password-file: re-run with the file flag")
    for _ in range(3):
        secret = prompt_tty_password()
        if secret is None:
            raise ValueError("headless without --restore-password-file: re-run with the file flag")
        if secret:
            fd, tmp = tempfile.mkstemp(prefix=".restic-pw.", suffix=".tmp")
            try:
                os.fchmod(fd, 0o600)
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(secret)
            finally:
                del secret
            return tmp, None, Path(tmp)
    raise ValueError("wrong password (3 attempts) — see paperkey copy (Bitwarden/paperkey custody, 40-E)")


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
    existing = [str(t) for t in target_paths(tag) if t.exists() and not t.is_symlink()]
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
                return fail("wrong password during restore — see paperkey copy (40-E custody)")
            tail = ((r.stderr or "").strip().splitlines() or ["restore failed"])[-1]
            return fail(f"restic restore failed: {tail}")
        # Preview = incoming file list.
        incoming = sorted(p.relative_to(staging) for p in staging.rglob("*") if not p.is_symlink() or True)
        incoming_files = [p for p in incoming if (staging / p).is_file() and not p.is_symlink()]
        log(f"preview: {len(incoming_files)} incoming files (first 50):")
        for rel in incoming_files[:50]:
            print(f"  + {rel}")
        if len(incoming_files) > 50:
            log(f"... and {len(incoming_files) - 50} more")
        if args.preview_only:
            log("preview-only: staged tree kept, nothing moved in")
            success = True
            return 0
        # Atomic move-in parents-first, hardened before swap, no --delete.
        home = real_home()
        by_depth = sorted(incoming, key=lambda p: len(p.parts))
        sudo_user = os.environ.get("SUDO_USER") or os.environ.get("USER") or ""
        try:
            import pwd

            pw = pwd.getpwnam(sudo_user) if sudo_user else pwd.getpwuid(os.getuid())
            uid, gid = pw.pw_uid, pw.pw_gid
        except (KeyError, ImportError):
            uid, gid = os.getuid(), os.getgid()
        harden_tree(staging, uid, gid)
        moved = 0
        for rel in by_depth:
            src = staging / rel
            dst = home / rel
            if src.is_symlink() or src.is_file():
                dst.parent.mkdir(parents=True, exist_ok=True)
                if dst.exists() or dst.is_symlink():
                    return fail(f"gate race: target appeared mid-restore: {dst}")
                os.replace(src, dst)
                moved += 1
            elif src.is_dir():
                dst.mkdir(parents=True, exist_ok=True)
        log(f"moved in {moved} files (additive only, no --delete)")
        success = True
        return 0
    finally:
        if success:
            shutil.rmtree(staging, ignore_errors=True)
        else:
            log(f"staging kept for forensics: {staging}")


def converge() -> None:
    """curated/ is never restored — pi-setup regenerates it."""
    for candidate in ("pi-setup", "pi_setup"):
        if shutil.which(candidate):
            log(f"converge: running `{candidate} auth/probe/fetch` to regenerate curated/")
            for sub in ("auth", "probe", "fetch"):
                r = subprocess.run([candidate, sub])
                if r.returncode != 0:
                    warn(f"`{candidate} {sub}` rc={r.returncode} — fix manually, then re-run")
            return
    log("converge: pi-setup not on PATH — run `pi-setup auth/probe/fetch` manually to regenerate curated/")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Standalone post-install.py restore (40-D D1)")
    p.add_argument("--restore-password-file", default="", help="path to file holding the repo password")
    p.add_argument("--password-command", default="", help="override password command (default: gopass reader)")
    p.add_argument("--allow-pending-reboot", action="store_true", help="proceed despite reboot latch (operator owns risk)")
    p.add_argument("--snapshot", default="latest", help="snapshot ID or 'latest' (default)")
    p.add_argument("--tag", default="", help="snapshot tag selector (e.g. vault for the store stream)")
    p.add_argument("--overwrite", default="if-changed", choices=["if-changed"], help="additive move-in only")
    p.add_argument("--preview-only", action="store_true", help="stage + preview, move nothing in")
    return p


def main(argv: list[str] | None = None) -> int:
    if not require_user("lib/40-restore.py", "python3 lib/40-restore.py"):
        return 1
    print(ASSUMPTION_BANNER, file=sys.stderr)
    parser = build_parser()
    args = parser.parse_args(argv)
    tag = args.tag or None

    failures = check_preconditions(args)
    if failures:
        for f in failures:
            print(f"40-restore: ERROR precondition: {f}", file=sys.stderr)
        return 1

    try:
        pw_file, pw_cmd, tmp_pw = resolve_password(args)
    except ValueError as exc:
        return fail(str(exc))
    cleanup_tmp = tmp_pw

    repo_env = None
    try:
        try:
            repo_env = build_repo_env(password_command=pw_cmd, password_file=pw_file)
        except (OSError, RuntimeError) as exc:
            return fail(f"repo env: {exc}")

        code, detail = preflight(repo_env)
        if code == 10:
            print(f"40-restore: ERROR preflight 10=missing: {detail}", file=sys.stderr)
            print("  List hosts with: restic snapshots (no auto-init)", file=sys.stderr)
            return 1
        if code == 11:
            print(f"40-restore: ERROR preflight 11=locked: {detail}", file=sys.stderr)
            print("  After confirming no live holder: restic unlock", file=sys.stderr)
            return 1
        if code == 12:
            return fail(f"preflight 12=wrong-pw: {detail} — see paperkey copy (40-E custody)")

        targets = target_paths(tag)
        conflicts = blank_iron_gate(targets)
        if conflicts:
            print("40-restore: ERROR blank-iron gate: populated targets refused (fail-stop):", file=sys.stderr)
            for c in conflicts:
                print(f"  conflict: {c}", file=sys.stderr)
            return 1

        safety_snapshot(repo_env.env, tag)
        rc = staged_restore(repo_env.env, args)
        if rc != 0:
            return rc
        if not args.preview_only:
            converge()
        log("done")
        return 0
    finally:
        if cleanup_tmp is not None:
            try:
                cleanup_tmp.unlink()
            except OSError:
                pass


if __name__ == "__main__":
    sys.exit(main())
