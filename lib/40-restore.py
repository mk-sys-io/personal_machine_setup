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
--restore-password-file, then --password-command (default the gopass
reader, covering re-restore on converged machines), then TTY prompt
<=3 attempts via /dev/tty. Headless without the
file flag fails naming it.

Exit codes (0/1 convention):
    0  restored (or preview-only OK, or SKIP when the vault store is
       already present and unlocks — the trailing RESULT status is
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
import getpass
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
    default_password_command,
    excludes_args,
    preflight,
    probe_gopass_field,
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
# ~/.local/share are never restored — the vault stream names the store
# subtree explicitly instead.
# "knowledge_base" is the notes-vault literal (backup-owned path; never
# derived from OBSIDIAN_VAULT_PATH — var retained as deploy pointer per
# the 12-D amendment, still never read here).
LIVEDATA_TARGETS = (
    "knowledge_base",
    "Videos/keep",
    ".ssh",
    ".gnupg",
)
# Vault-stream consumer target: the store subtree only (never wholesale
# ~/.local/share). GPG key-export import (gpg --import + ownertrust) is the
# ordered first path per 40-D1c (import before store use); the producer owns
# the export per ADR-002. Import runs after move-in (files must land first),
# before decrypt-verify.
VAULT_STORE_SUBTREE = ".local/share/gopass"
VAULT_KEY_EXPORT_SUBTREE = ".local/share/gpg-export"
VAULT_EXPORT_SECRET_KEYS = "secret-subkeys.asc"
VAULT_EXPORT_OWNERTRUST = "ownertrust.txt"

# Agent posture per 40-D2 V0 (monthly TTLs, terminal-only pinentry). Pinentry
# is curses, not gnome3: the vault restore runs 3rd in the trunk and a bare
# netinstall has no display to draw on. Written only-if-absent — a live-home
# config is never overwritten, so an operator who tuned their own posture
# keeps it.
AGENT_CONF_LINES = (
    "default-cache-ttl 2592000\n"
    "max-cache-ttl 2592000\n"
    "pinentry-program /usr/bin/pinentry-curses\n"
    "allow-preset-passphrase\n"
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

    Every nonzero return in this module funnels through here (staged_restore,
    import_vault_key and main alike), so emitting here is what guarantees the
    trailer exists on every failure — see the exit-code contract above.
    """
    print(f"40-restore: ERROR {msg}", file=sys.stderr)
    emit_result("FAIL", False, msg)
    return 1


def real_home() -> Path:
    home = os.environ.get("HOME")
    return Path(home) if home else Path.home()


def ensure_agent_posture() -> None:
    """Write ~/.gnupg/gpg-agent.conf if absent, then reload the agent.

    Hard precondition for the vault stream, so a failure here is fatal by
    the caller rather than a warning: without the file gpg-agent falls back
    to whatever gnupg happens to depend on, and decrypt-verify degrades to a
    warning that says nothing about why. Only-if-absent is preserved — this
    never overwrites a live home's posture.

    The write is 600 and the reloadagent is best-effort: a fresh file is
    picked up on the next agent spawn regardless, so a reload failure is not
    worth aborting a restore over.
    """
    conf = real_home() / ".gnupg" / "gpg-agent.conf"
    if conf.is_file():
        return
    try:
        conf.parent.mkdir(parents=True, exist_ok=True)
        conf.write_text(AGENT_CONF_LINES, encoding="utf-8")
        os.chmod(conf, 0o600)
    except OSError as exc:
        raise RuntimeError(f"cannot write {conf}: {exc}") from exc
    log(f"wrote {conf} (only-if-absent)")
    try:
        subprocess.run(["gpg-connect-agent", "reloadagent"], capture_output=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        warn(f"gpg-connect-agent reloadagent failed (warn-and-continue): {exc}")


def target_paths(tag: str | None) -> list[Path]:
    """Paths this restore owns, for two purposes: the blank-iron gate and the
    pre-restore-<ts> safety snapshot.

    Vault returns both subtrees. The key-export dir is included because
    import_vault_key reads it and 40-backup.py ships it, so a populated
    export dir must not slip past the gate; the same widening means the
    safety snapshot now covers it too.
    """
    home = real_home()
    if tag == "vault":
        return [home / VAULT_STORE_SUBTREE, home / VAULT_KEY_EXPORT_SUBTREE]
    return [home / t for t in LIVEDATA_TARGETS]


# ── vault-stream idempotency ─────────────────────────────────────────────────
#
# Vault only. The store arriving already populated is the normal state of a
# converged machine, and re-restoring it would trip the blank-iron gate for no
# reason. The probe is `gopass ls --flat`: it lists entries and never reads a
# secret, so the SKIP decision costs nothing but a directory walk.
#
# "populated but does not unlock" was abort-grade in the old store-bootstrap
# step, and abort-grade on a transient agent-cache state is a bad trade: the
# cached passphrase is exactly the thing that expires. It is now a prompt-and-
# retry (decision F3), bounded at 2 attempts, with a recovery recipe on the
# failure path.

GOPASS_PROBE_TIMEOUT = 120
UNLOCK_RETRIES = 2


def vault_store_populated() -> bool:
    """True iff the store dir holds at least one encrypted entry.

    gopass auto-creates an empty skeleton (`stores/root/`) on first use —
    including failed reads by earlier trunk steps — so mere dir-presence is
    not evidence of a store. Only `*.gpg` counts.
    """
    store = real_home() / VAULT_STORE_SUBTREE
    if not store.is_dir():
        return False
    try:
        return any(store.rglob("*.gpg"))
    except OSError:
        return False


def vault_store_unlocks() -> bool:
    """True iff gopass can list entries (no secret is ever read)."""
    gopass = shutil.which("gopass")
    if gopass is None:
        return False
    try:
        probe = subprocess.run(
            [gopass, "ls", "--flat"],
            capture_output=True,
            text=True,
            timeout=GOPASS_PROBE_TIMEOUT,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    if probe.returncode != 0:
        return False
    return any(ln.strip() for ln in (probe.stdout or "").splitlines())


def _reload_gpg_agent() -> None:
    """Drop the agent's cached passphrase so pinentry asks again.

    We do NOT prompt for the GPG passphrase ourselves: main has already set
    GPG_TTY to a real node, so pinentry-curses draws it on /dev/tty. Feeding
    the passphrase to the agent by hand would mean gpg-preset-passphrase,
    which decision F1 rules out.
    """
    try:
        subprocess.run(["gpg-connect-agent", "reloadagent"], capture_output=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        warn(f"gpg-connect-agent reloadagent failed ({exc}) — the agent keeps its cached passphrase")


def check_vault_store() -> tuple[int, str]:
    """(rc, message) for the vault stream. Never raises.

    rc 0 -> proceed (store absent). rc 2 -> SKIP (populated and unlocking).
    rc 1 -> already trailered by fail(), with a recovery recipe.

    Empty store -> proceed. Populated and unlocking -> SKIP. Populated but
    locked -> reload the agent and re-probe UNLOCK_RETRIES times; success
    joins the SKIP path, exhaustion is a named failure with a recipe. The
    operator is present: install.py refuses to start without a terminal (D1)
    and main has already refused without a resolvable GPG_TTY.
    """
    if not vault_store_populated():
        return 0, "store absent"
    if vault_store_unlocks():
        return 2, "store present and unlocks"
    for attempt in range(1, UNLOCK_RETRIES + 1):
        log(f"store present but does not unlock — retry {attempt}/{UNLOCK_RETRIES} after dropping the agent passphrase cache")
        _reload_gpg_agent()
        if vault_store_unlocks():
            return 2, "store present and unlocks after a passphrase re-prompt"
    return fail(
        f"store present but gopass ls still fails after {UNLOCK_RETRIES} attempts. "
        "Recovery: gpg-connect-agent killall; gpg --list-secret-keys "
        "(the key must be present, or re-import from the key-export dir); "
        "then re-run ./install.py"
    ), "store present but locked"


def check_vault_prerequisites() -> list[str]:
    """Vault-stream agent posture + binary gates. Verify-only, never converge.

    Kept out of check_preconditions on purpose: this must run BEFORE
    check_vault_store, which invokes gopass. Lumping them together would
    spend two pointless unlock retries on a machine that simply has no
    gopass, and then report "gopass ls still fails" instead of the actual
    fix. Ordering beats tidiness here.
    """
    failures: list[str] = []
    try:
        ensure_agent_posture()
    except RuntimeError as exc:
        failures.append(str(exc))
    for binary, owner in (("gopass", "packages/apt.txt"), ("pinentry-curses", "packages/apt.txt")):
        if shutil.which(binary) is None:
            failures.append(f"{binary} missing — re-run ./install.py (ships it via {owner})")
    return failures


def check_preconditions(args: argparse.Namespace) -> list[str]:
    """Verify-only, never converge. Each failure names the fix."""
    failures: list[str] = []
    if restic_binary() is None:
        failures.append("restic binary missing — re-run ./install.py (phase 40-A ships restic)")
    # Skeleton target dirs exist (never auto-created here). Vault-only
    # restores (--tag vault, trunk seam per ADR-013) run before the
    # skeleton step, so these livedata checks are skipped for that tag —
    # the store needs only its own two subtrees, whose destination
    # parents are created by staged move-in.
    home = real_home()
    if getattr(args, "tag", "") != "vault":
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
    # Reboot pending is fail-stop unless the operator owns the risk. A vault
    # restore is exempt outright: it moves two dotdirs and never touches
    # kernel state, so a kernel installed by 20-packages is irrelevant to it.
    latch = Path.home() / ".config" / "install" / ".install-need-reboot"
    if latch.is_file() or Path("/run/reboot-required").is_file():
        if getattr(args, "tag", "") == "vault":
            warn("--tag vault: reboot pending, proceeding — a vault restore moves two dotdirs and never touches kernel state")
        elif args.allow_pending_reboot:
            warn("--allow-pending-reboot: reboot pending, operator owns the risk (logged)")
        else:
            failures.append("reboot pending (latch or /run/reboot-required) — reboot and verify-stable first")
    return failures


def prompt_tty(label: str) -> str | None:
    """Prompt on /dev/tty with `label`. Returns None when there is no usable terminal.

    Never use open("/dev/tty", "r+"): Python's buffered text I/O needs a
    seekable stream and a tty is not, so it raises UnsupportedOperation --
    an OSError subclass -- which the old bare `except OSError` swallowed,
    making "no terminal" indistinguishable from "prompt machinery broken".
    os.open gives an unbuffered fd; os.fdopen in "w" with buffering=1 is
    line-buffered for the prompt write only. getpass opens its own
    O_NOCTTY handle for the termios echo-off, so the dup is safe to skip.
    """
    try:
        fd = os.open("/dev/tty", os.O_RDWR)
    except OSError as exc:
        log(f"no usable terminal for the password prompt: {exc}")
        return None
    try:
        with os.fdopen(fd, "w", buffering=1) as tty:
            tty.write(label)
            tty.flush()
            secret = getpass.getpass("", stream=tty)
    except OSError as exc:
        # Distinct from "no terminal": the tty opened but the prompt or
        # read failed. Never silently degrade to None here.
        log(f"password prompt failed on an open /dev/tty: {exc}")
        return None
    return secret.strip() or None


def resolve_tty_path() -> str:
    """Return a terminal path GPG_TTY can actually resolve, else "".

    Order:
      1. os.ttyname(0) when stdin is a tty. Valid only because Phase 1
         removed start_new_session -- stdin stays the real terminal.
      2. readlink /proc/self/fd/0, accepted only if it lands under
         /dev/pts/ or /dev/tty* (a real node gpg-agent can open).
      3. inherited $GPG_TTY (dotfiles/bashrc exports it under a `tty -s`
         guard).

    NEVER return the literal string "/dev/tty": gpg-agent is a daemon with
    no controlling terminal, so /dev/tty does not resolve there. Measured:
    os.ttyname(os.open("/dev/tty")) returns exactly "/dev/tty", while
    os.ttyname(0) returns the concrete /dev/pts/N that gpg-agent needs.
    """
    try:
        if os.isatty(0):
            name = os.ttyname(0)
            if name and name != "/dev/tty":
                return name
    except OSError:
        pass
    try:
        link = os.readlink("/proc/self/fd/0")
    except OSError:
        link = ""
    if link.startswith("/dev/pts/") or (
        link.startswith("/dev/tty") and link != "/dev/tty"
    ):
        return link
    inherited = os.environ.get("GPG_TTY", "")
    if inherited and inherited != "/dev/tty":
        return inherited
    return ""


def resolve_password(args: argparse.Namespace) -> tuple[str | None, str | None, Path | None]:
    """Return (password_file, password_command, temp_to_cleanup).

    Chain: --restore-password-file -> --password-command -> TTY <=3.
    Headless without the file flag fails naming it.
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
    # NOTE: this guard can never fire. Path("/dev/tty").exists() is True on
    # any Linux box (the device node exists whether or not a process has a
    # controlling terminal), so the headless case falls through to the
    # prompt below, prompt_tty() returns None, and the raise at the
    # `if secret is None` line is what actually reports headless. Do NOT
    # "fix" this by flipping `and` to `or` -- that would be equally inert.
    if not sys.stdin.isatty() and not Path("/dev/tty").exists():
        raise ValueError("headless without --restore-password-file: re-run with the file flag")
    for _ in range(3):
        secret = prompt_tty("40-restore: restic repo password (Bitwarden-held string): ")
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
    raise ValueError("wrong password (3 attempts) — see Bitwarden vault (40-E custody)")


def resolve_b2_credentials() -> tuple[str, str]:
    """(key_id, secret_access_key) for the S3 backend.

    Chain: gopass `services/b2` probe -> prompt on /dev/tty, mirroring
    resolve_password's shape. build_repo_env used to read services/b2
    unconditionally, which is circular on blank iron: the store arrives
    *through* this restore, so the thing that authorises the read cannot be
    the read. Both values live in Bitwarden (backup.md custody table) and
    both are prompted; neither is a filename or a flag, so nothing new is
    added to the command line.

    Probed per field, so a half-populated entry prompts only for what is
    missing instead of re-asking for both. Never logged: the values reach
    the restic subprocess env and nowhere else.
    """
    key_id = probe_gopass_field("services/b2", "keyID")
    secret = probe_gopass_field("services/b2", "applicationKey")
    if key_id and secret:
        return key_id, secret
    warn("gopass cannot supply services/b2 — prompting for the Bitwarden-held values")
    if not key_id:
        key_id = prompt_tty("40-restore: B2 keyID (Bitwarden-held): ") or ""
    if not secret:
        secret = prompt_tty("40-restore: B2 applicationKey (Bitwarden-held): ") or ""
    if not key_id or not secret:
        raise ValueError("B2 credentials incomplete — see the Bitwarden vault (services/b2)")
    return key_id, secret


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
    cmd = ["restic", *base_args(), *excludes_args(), "backup", "--tag", f"pre-restore-{ts}", *existing]
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


def import_vault_key(home: Path) -> int:
    """40-D2 V2: ordered GPG-import leg for `--tag vault` restores.

    Imports the secret-subkey + ownertrust from the restored key-export
    dir, then best-effort decrypt-verifies the store. Runs after move-in
    (files must land first). Fail-loud on import errors;
    decrypt-verify is warn-and-continue (store contents vary).
    """
    export_dir = home / VAULT_KEY_EXPORT_SUBTREE
    if not export_dir.is_dir():
        warn(f"no key-export dir at ~/{VAULT_KEY_EXPORT_SUBTREE} (pre-V2 snapshot?) — import via USB Step-0b instead")
        return 0
    sec = export_dir / VAULT_EXPORT_SECRET_KEYS
    trust = export_dir / VAULT_EXPORT_OWNERTRUST
    if not sec.is_file():
        return fail(f"key-export dir present but {VAULT_EXPORT_SECRET_KEYS} missing — re-run the Step-0b/V2 export")
    r = subprocess.run(["gpg", "--import", str(sec)], capture_output=True, text=True, timeout=300)
    if r.returncode != 0:
        tail = ((r.stderr or "").strip().splitlines() or ["gpg --import failed"])[-1]
        return fail(f"gpg --import failed: {tail}")
    log(f"imported GPG secret-subkey from ~/{VAULT_KEY_EXPORT_SUBTREE}/{VAULT_EXPORT_SECRET_KEYS}")
    if trust.is_file():
        r = subprocess.run(["gpg", "--import-ownertrust", str(trust)], capture_output=True, text=True, timeout=300)
        if r.returncode != 0:
            tail = ((r.stderr or "").strip().splitlines() or ["ownertrust import failed"])[-1]
            return fail(f"gpg --import-ownertrust failed: {tail}")
        log(f"imported ownertrust from ~/{VAULT_KEY_EXPORT_SUBTREE}/{VAULT_EXPORT_OWNERTRUST}")
    else:
        warn(f"{VAULT_EXPORT_OWNERTRUST} absent — ownertrust left at TOFU defaults")
    # Best-effort decrypt-verify: a successful `gopass show` proves the
    # imported key unlocks the restored store. Never log the secret.
    gopass = shutil.which("gopass")
    store = home / VAULT_STORE_SUBTREE
    if gopass and store.is_dir():
        probe = subprocess.run([gopass, "ls", "--flat"], capture_output=True, text=True, timeout=120)
        entries = [ln.strip() for ln in (probe.stdout or "").splitlines() if ln.strip()] if probe.returncode == 0 else []
        if entries:
            ver = subprocess.run([gopass, "show", "--output", entries[0]], capture_output=True, text=True, timeout=120)
            if ver.returncode == 0:
                log(f"decrypt-verify: `gopass show` unlocked '{entries[0]}' (secret not logged)")
            else:
                warn("decrypt-verify: `gopass show` failed — key/store mismatch? fix manually")
        else:
            warn("decrypt-verify: store restored but empty — nothing to unlock yet")
    else:
        warn("decrypt-verify skipped: gopass or store absent")
    return 0


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
        emit_result("FAIL", False, "refused to run as root")
        return 1
    print(ASSUMPTION_BANNER, file=sys.stderr)
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit:
        # argparse exits 2 on bad flags, and opslog.map_rc maps 2 to SKIP --
        # so a typo'd flag would be reported to the trunk as a skip, i.e. a
        # success. Emit FAIL and retire the 2 (nothing reads it: the old
        # store-bootstrap wrapper collapsed every nonzero to 1).
        emit_result("FAIL", False, "bad arguments")
        return 1
    tag = args.tag or None

    # 40-D2 V0 agent posture: in-terminal pinentry needs GPG_TTY in-process.
    # stderr is unusable here: opslog.run() merges it into a PIPE
    # (stderr=subprocess.STDOUT), so os.ttyname() on it always raises.
    # Resolve from the inherited stdin instead -- Phase 1 removed
    # start_new_session, so stdin is still the real terminal.
    tty = resolve_tty_path()
    if tty:
        os.environ["GPG_TTY"] = tty
    else:
        msg = (
            "no terminal to draw pinentry on: stdin is not a tty and "
            "GPG_TTY is unset. Re-run from an interactive terminal "
            "(see docs/recovery/new-machine.md) or export GPG_TTY=$(tty)."
        )
        print(f"40-restore: ERROR {msg}", file=sys.stderr)
        emit_result("FAIL", False, msg)
        return 1

    # Vault agent posture + binary gates, then the idempotency check. Both
    # run before check_preconditions: the trunk runs this step 3rd
    # (ADR-013) and the standalone path had no posture code at all, so the
    # same command behaved differently depending on who invoked it.
    if tag == "vault":
        vault_failures = check_vault_prerequisites()
        if vault_failures:
            for f in vault_failures:
                print(f"40-restore: ERROR precondition: {f}", file=sys.stderr)
            emit_result("FAIL", False, f"vault precondition(s) failed: {vault_failures[0]}")
            return 1
        if not args.preview_only:
            store_rc, note = check_vault_store()
            if store_rc == 2:
                emit_result("SKIP", False, note)
                return 2
            if store_rc != 0:
                return store_rc  # already trailered by fail()

    failures = check_preconditions(args)
    if failures:
        for f in failures:
            print(f"40-restore: ERROR precondition: {f}", file=sys.stderr)
        emit_result("FAIL", False, f"{len(failures)} precondition(s) failed: {failures[0]}")
        return 1

    try:
        pw_file, pw_cmd, tmp_pw = resolve_password(args)
    except ValueError as exc:
        return fail(str(exc))
    cleanup_tmp = tmp_pw

    repo_env = None
    try:
        try:
            b2_key_id, b2_secret = resolve_b2_credentials()
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

        targets = target_paths(tag)
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
        if tag == "vault" and not args.preview_only:
            rc = import_vault_key(real_home())
            if rc != 0:
                return rc  # already trailered by fail() inside import_vault_key
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
