#!/usr/bin/env python3
"""Merge the Brave NTP preferences seed into the live profile.

Brave writes Preferences on exit, so a merge while Brave is running would be
silently overwritten — this module terminates Brave gracefully first
(SIGTERM → poll for full exit → SIGKILL stragglers), then merges.

On a bare machine Brave has never been launched, so Default/Preferences does
not exist. Rather than skip, this module runs Brave once in headless mode to
materialize the profile, then merges the seed. Headless mode needs no display
server, so it works from the installer's bare TTY. If the headless launch
cannot start, the seed is written as a new Preferences file instead.

The brave-browser wrapper always exits 0 (`"$HERE/brave" "$@" || true`), so a
created, parseable Preferences file — not the wrapper's return code — is the
authority on success.

Run standalone: python3 lib/45-brave.py
Wired into install.py as module 45.

Exit codes:
    0  merged (bootstrapping the profile headlessly first when absent)
    1  failure (Brave not installed, seed missing, merge/verify failed)
"""
from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "lib" / "helpers"))  # for preconditions (ADR-011)

from preconditions import require_user  # noqa: E402  (refuse-root guard)

SEED = REPO_ROOT / "dotfiles" / "browsers" / "brave" / "preferences.seed.json"
PREFS = Path.home() / ".config" / "BraveSoftware" / "Brave-Browser" / "Default" / "Preferences"
PROFILE_ROOT = PREFS.parent.parent

KILL_POLL_SECONDS = 10
KILL_POLL_INTERVAL = 0.5
HEADLESS_TIMEOUT = 60
PROFILE_SETTLE_SECONDS = 1.0


def _stop_process_group(proc: subprocess.Popen[bytes]) -> None:
    """Terminate the headless browser and any zygotes in its session."""
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except OSError:
        return
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except OSError:
        pass
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass


def brave_binary() -> str | None:
    """Return the real Brave ELF when resolvable, else the wrapper path.

    The wrapper swallows the browser's exit status, so prefer the sibling ELF
    (`.../brave`) whenever the packaged wrapper points at one.
    """
    wrapper = shutil.which("brave-browser")
    if wrapper is None:
        return None
    real = Path(wrapper).resolve()
    elf = real.with_name("brave")
    if elf.is_file() and os.access(elf, os.X_OK):
        return str(elf)
    return wrapper


def brave_running() -> bool:
    return subprocess.run(["pgrep", "-x", "brave"], capture_output=True).returncode == 0


def terminate_brave() -> None:
    if not brave_running():
        return
    print("brave-preferences: Brave running — terminating gracefully")
    subprocess.run(["pkill", "-TERM", "-x", "brave"], check=False)
    waited = 0.0
    while waited < KILL_POLL_SECONDS:
        if not brave_running():
            return
        time.sleep(KILL_POLL_INTERVAL)
        waited += KILL_POLL_INTERVAL
    print("brave-preferences: Brave did not exit — forcing kill")
    subprocess.run(["pkill", "-KILL", "-x", "brave"], check=False)
    time.sleep(1)


def load_seed() -> dict:
    with open(SEED, encoding="utf-8") as f:
        seed = json.load(f)
    if not isinstance(seed, dict):
        raise ValueError("seed must be a JSON object")
    return seed


def prefs_ready() -> bool:
    if not PREFS.is_file():
        return False
    try:
        with open(PREFS, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return False
    return isinstance(data, dict)


def bootstrap_profile(binary: str) -> None:
    """Materialize Default/Preferences with one unattended headless launch.

    Brave 156 does not reliably exit from ``--dump-dom`` on a bare TTY, but it
    writes a complete, parseable Preferences file during startup. Watch for
    that file, then terminate the process group ourselves.
    """
    PROFILE_ROOT.mkdir(parents=True, exist_ok=True, mode=0o700)
    cmd = [
        binary,
        "--headless",
        "--disable-gpu",
        "--disable-dev-shm-usage",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-background-networking",
        "--disable-component-update",
        "--disable-sync",
        "--password-store=basic",
        f"--user-data-dir={PROFILE_ROOT}",
        "--dump-dom",
        "about:blank",
    ]
    print(f"brave-preferences: bootstrapping headless profile at {PROFILE_ROOT}")
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=out,
            stderr=err,
            start_new_session=True,
        )
        ready = False
        deadline = time.monotonic() + HEADLESS_TIMEOUT
        while time.monotonic() < deadline:
            if prefs_ready():
                ready = True
                break
            if proc.poll() is not None:
                break
            time.sleep(0.25)
        if ready:
            time.sleep(PROFILE_SETTLE_SECONDS)
        _stop_process_group(proc)
        if not ready and prefs_ready():
            ready = True
        out.seek(0)
        stdout = out.read().decode("utf-8", "replace")
        err.seek(0)
        stderr = err.read().decode("utf-8", "replace")

    if brave_running():
        terminate_brave()
    if not ready:
        details = " | ".join((stderr or stdout).strip().splitlines()[-3:])
        if not details:
            details = "no output"
        raise RuntimeError(
            f"headless Brave did not create a valid {PREFS} "
            f"(rc={proc.returncode}): {details}"
        )


def seed_fresh_profile(seed: dict) -> None:
    PREFS.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    write_prefs(seed, 0o600)


def deep_merge(base: dict, overlay: dict) -> None:
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            deep_merge(base[key], value)
        else:
            base[key] = value


def verify_seed(base: dict, overlay: dict, path: str = "") -> None:
    for key, value in overlay.items():
        current_path = f"{path}.{key}" if path else key
        if isinstance(value, dict):
            if not isinstance(base.get(key), dict):
                raise ValueError(f"{current_path} missing or wrong type")
            verify_seed(base[key], value, current_path)
        elif base.get(key) != value:
            raise ValueError(f"{current_path} = {base.get(key)!r}, expected {value!r}")


def write_prefs(prefs: dict, mode: int) -> None:
    fd, tmp = tempfile.mkstemp(dir=PREFS.parent, prefix=".Preferences.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(prefs, f, indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, PREFS)
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def main(argv: list[str]) -> int:
    if {"-h", "--help"} & set(argv[1:]):
        print("Merge the Brave NTP preferences seed into the live profile.")
        print("usage: python3 lib/45-brave.py")
        return 0
    if not require_user("lib/45-brave.py", "python3 lib/45-brave.py"):
        return 1
    binary = brave_binary()
    if binary is None:
        print(
            "brave-preferences: ERROR Brave not installed "
            "(brave-browser not in PATH)",
            file=sys.stderr,
        )
        return 1

    if not SEED.exists():
        print(f"brave-preferences: ERROR seed missing: {SEED}", file=sys.stderr)
        print(
            f"RESULT {json.dumps({'status': 'FAIL', 'changed': False, 'message': 'seed missing'})}"
        )
        return 1

    terminate_brave()

    warnings: list[str] = []
    fresh = False
    bootstrap_ok = False
    try:
        if not PREFS.is_file():
            try:
                bootstrap_profile(binary)
                bootstrap_ok = True
                fresh = True
            except (OSError, RuntimeError) as exc:
                warning = f"headless bootstrap failed: {exc}"
                warnings.append(warning)
                print(f"brave-preferences: WARN {warning}", file=sys.stderr)
                seed_fresh_profile(load_seed())
                fresh = True

        if fresh and not bootstrap_ok:
            print(f"brave-preferences: seeded fresh profile at {PREFS}")
        elif fresh:
            print(f"brave-preferences: bootstrapped profile at {PREFS}")
        else:
            backup = f"{PREFS}.bak-{int(time.time())}"
            shutil.copy2(PREFS, backup)
            print(f"brave-preferences: backed up to {backup}")

        seed = load_seed()
        with open(PREFS, encoding="utf-8") as f:
            prefs = json.load(f)
        mode = os.stat(PREFS).st_mode

        deep_merge(prefs, seed)
        write_prefs(prefs, mode)

        with open(PREFS, encoding="utf-8") as f:
            merged = json.load(f)
        verify_seed(merged, seed)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"brave-preferences: ERROR {exc}", file=sys.stderr)
        trailer = {
            "status": "FAIL",
            "changed": False,
            "message": f"merge/verify failed: {exc}",
        }
        print(f"RESULT {json.dumps(trailer)}")
        return 1

    if bootstrap_ok:
        message = "bootstrapped profile and merged seed"
    elif fresh:
        message = "seeded fresh profile (headless bootstrap unavailable)"
    else:
        message = "merged and verified"
    print(f"brave-preferences: {message}")
    trailer = {"status": "OK", "changed": True, "message": message}
    if warnings:
        trailer["warnings"] = warnings
    print(f"RESULT {json.dumps(trailer)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
