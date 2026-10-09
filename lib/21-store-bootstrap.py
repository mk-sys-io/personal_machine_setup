#!/usr/bin/env python3
"""Trunk store bootstrap (Phase 20-F, owner 20-F).

Runs 3rd in ./install.py (after 20-packages, before 22-wifi-migrate):
restores ONLY the `--tag vault` stream (gopass store + GPG key-export
import) via lib/40-restore.py, so no downstream step ever observes a
missing store. Livedata restore stays standalone post-install per
ADR-012; this seam is the ADR-013 scoped exception.

Idempotent: store present + unlockable -> SKIP. Abort-grade: any
failure -> FAIL (install.py exits 1). Operator prompts (Bitwarden-held
restic password, GPG passphrase) happen on /dev/tty inside the child.

Stdlib only. Prints a RESULT {json} trailer for the install.py runner.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "lib" / "helpers"))

from preconditions import require_user  # noqa: E402  (refuse-root guard, ADR-011)

STORE_SUBTREE = Path(".local/share/gopass")

# Agent posture per 40-D2 V0 (monthly TTLs, terminal-only pinentry).
# Written only-if-absent; a live-home config is never overwritten here.
AGENT_CONF_LINES = (
    "default-cache-ttl 2592000\n"
    "max-cache-ttl 2592000\n"
    "pinentry-program /usr/bin/pinentry-curses\n"
    "allow-preset-passphrase\n"
)


def _home() -> Path:
    home = os.environ.get("HOME")
    return Path(home) if home else Path.home()


def _store_has_secrets() -> bool:
    """True iff the store dir holds at least one encrypted entry.

    gopass auto-creates an empty skeleton (`stores/root/`) on first
    use — including failed reads by earlier trunk steps — so mere
    dir-presence is not evidence of a store. Only `*.gpg` counts.
    """
    store = _home() / STORE_SUBTREE
    if not store.is_dir():
        return False
    try:
        return any(store.rglob("*.gpg"))
    except OSError:
        return False


def _store_unlocks() -> bool:
    """True iff the store exists and gopass can list entries (secret never read)."""
    gopass = shutil.which("gopass")
    if gopass is None:
        return False
    try:
        probe = subprocess.run(
            [gopass, "ls", "--flat"], capture_output=True, text=True, timeout=120
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    if probe.returncode != 0:
        return False
    return any(ln.strip() for ln in (probe.stdout or "").splitlines())


def _ensure_agent_posture() -> None:
    conf = _home() / ".gnupg" / "gpg-agent.conf"
    if conf.is_file():
        return
    try:
        conf.parent.mkdir(parents=True, exist_ok=True)
        conf.write_text(AGENT_CONF_LINES, encoding="utf-8")
        os.chmod(conf, 0o600)
        print("21-store-bootstrap: wrote ~/.gnupg/gpg-agent.conf (only-if-absent)")
    except OSError as exc:
        print(f"21-store-bootstrap: WARN cannot write gpg-agent.conf: {exc}")
        return
    try:
        subprocess.run(["gpg-connect-agent", "reloadagent"], capture_output=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"21-store-bootstrap: WARN reloadagent failed (warn-and-continue): {exc}")


def main(argv: list[str] | None = None) -> int:
    if not require_user("lib/21-store-bootstrap.py", "python3 lib/21-store-bootstrap.py"):
        return 1
    parser = argparse.ArgumentParser(description="Trunk store bootstrap (20-F)")
    parser.add_argument("--restore-password-file", default="",
                        help="forwarded to lib/40-restore.py (fixture drills / headless)")
    args = parser.parse_args(argv)
    if _store_has_secrets():
        if _store_unlocks():
            print("21-store-bootstrap: store present and unlocks — skipping")
            print('RESULT {"status": "SKIP", "changed": false, '
                  '"message": "store present and unlocks"}')
            return 2
        print("21-store-bootstrap: ERROR store present but does not unlock — fix manually", file=sys.stderr)
        print('RESULT {"status": "FAIL", "changed": false, '
              '"message": "store present but gopass ls fails"}')
        return 1
    _ensure_agent_posture()
    child = [sys.executable, str(REPO_ROOT / "lib" / "40-restore.py"), "--tag", "vault"]
    if args.restore_password_file:
        child += ["--restore-password-file", args.restore_password_file]
    print("21-store-bootstrap: restoring --tag vault (Bitwarden-held password + GPG passphrase prompt follows)")
    try:
        # No capture: the child prompts via /dev/tty and streams its own log.
        rc = subprocess.run(child).returncode
    except OSError as exc:
        print(f"21-store-bootstrap: ERROR cannot run 40-restore.py: {exc}", file=sys.stderr)
        print('RESULT {"status": "FAIL", "changed": false, "message": "cannot run 40-restore.py"}')
        return 1
    if rc != 0:
        print(f"21-store-bootstrap: ERROR vault restore failed (rc={rc})", file=sys.stderr)
        print(f'RESULT {{"status": "FAIL", "changed": false, '
              f'"message": "vault restore rc={rc}"}}')
        return 1
    print("21-store-bootstrap: vault stream restored")
    print('RESULT {"status": "OK", "changed": true, '
          '"message": "vault stream restored"}')
    return 0


if __name__ == "__main__":
    sys.exit(main())
