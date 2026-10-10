#!/usr/bin/env python3
"""Ark system deploy: static files + state (offline half of 60-ark.sh).

Ports check_retired_paths → backup_existing → deploy_adapters →
deploy_ark_scripts → deploy_ark_domains → deploy_nftables → deploy_polkit
→ deploy_resolv → deploy_systemd → deploy_sysctl → deploy_bin_scripts →
deploy_ark → deploy_ark_perms → deploy_timeshift (60-ark.sh:622-644
relative order). Rendered-policy + live-actuation steps (deploy_sudoers,
subst_templates, deploy_system_dns, deploy_blocklist, deploy_browser_
policies, validate_configs, reload_services) live in 61-ark-policy.py.

Runs as your normal user and escalates internally via sudo (never run
this module itself as root). No network calls anywhere in this module.

Run standalone: python3 lib/60-ark-deploy.py
Wired into install.py as 60-ark-deploy

Exit codes:
    0  deployed
    1  failure (fail-closed; False/empty never means failure)
Stdlib only.
"""

from __future__ import annotations

import os
import shlex
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "lib" / "helpers"))  # for preconditions (ADR-011)

import ark_common  # noqa: E402
from ark_common import (  # noqa: E402
    deploy_file,
    log,
    log_error,
    log_ok,
    log_step,
    log_warn,
    priv_exists,
    run_priv,
)
from preconditions import require_user  # noqa: E402  (refuse-root guard)


def _run_fatal(*args: str) -> str:
    """run_priv that raises on nonzero (bash set -e parity). Returns output."""
    rc, out = run_priv(*args)
    if rc != 0:
        raise RuntimeError(f"{' '.join(args)} failed (rc={rc}): {out.strip()}")
    return out


def _mkdir_p(*dirs: str) -> None:
    _run_fatal("mkdir", "-p", *dirs)


def check_retired_paths(data_path: str) -> None:
    """Fail closed if retired v1 paths linger (deploy never removes files)."""
    log_step("Checking for retired v1 paths")
    # Bare tests correct: /etc/systemd/system and scripts/ are 755 (user-readable).
    found = False
    for p in (
        "/etc/systemd/system/ark-transition.timer",
        "/etc/systemd/system/ark-transition.service",
        f"{data_path}/scripts/ark-transition.sh",
    ):
        if os.path.exists(p) or os.path.islink(p):
            log_error(f"Retired path still present: {p}")
            found = True
    if found:
        log_error("Manual pre-install cleanup required: stop + disable the")
        log_error("ark-transition timer/service and delete the transition")
        log_error("script, then re-run install.py (deploy never removes files).")
        raise RuntimeError("retired paths present")
    log_ok("No retired ark-transition paths present")


def backup_existing(data_path: str) -> None:
    """Best-effort pre-deploy backup to /tmp. Never fails, never sets changed."""
    backup_dir = f"/tmp/ark-backup-{int(time.time())}"
    os.makedirs(backup_dir, exist_ok=True)
    log(f"Backing up to {backup_dir}")
    # Bare test correct: /etc/nftables.conf is 644 under 755 /etc (user-readable).
    if os.path.isfile("/etc/nftables.conf"):
        rc, _ = run_priv("cp", "/etc/nftables.conf", backup_dir + "/")
        if rc != 0:
            log_warn("backup: cp /etc/nftables.conf failed")
    for f in ark_common.SUDOERS_FILES:
        if priv_exists("-f", f"/etc/sudoers.d/{f}"):
            rc, _ = run_priv("cp", f"/etc/sudoers.d/{f}", backup_dir + "/")
            if rc != 0:
                log_warn(f"backup: cp /etc/sudoers.d/{f} failed")
    # Bare test correct: /opt/ark itself is 755 (only subtrees are 750).
    if os.path.isdir(data_path):
        rc, _ = run_priv("cp", "-r", data_path, backup_dir + "/")
        if rc != 0:
            log_warn(f"backup: cp {data_path} failed")
    log_ok("Backup complete")


def deploy_adapters(lib_path: str) -> bool:
    """Deploy ark helper scripts. Returns True if anything changed."""
    log_step("Deploying adapters")
    changed = False
    _mkdir_p(lib_path)
    changed |= deploy_file(REPO_ROOT / "etc/ark/adapters/discover-session.py", f"{lib_path}/discover-session.py")
    changed |= deploy_file(REPO_ROOT / "etc/ark/adapters/clipboard-clear.sh", f"{lib_path}/clipboard-clear.sh", "755")
    changed |= deploy_file(REPO_ROOT / "etc/ark/adapters/terminal", f"{lib_path}/terminal", "755")
    log_ok(f"Adapters deployed to {lib_path}")
    return changed


def deploy_ark_scripts(data_path: str) -> bool:
    """Deploy ark scripts + sub-packages + immutable wrapper. True if changed."""
    log_step("Deploying ark scripts")
    changed = False
    scripts = f"{data_path}/scripts"
    _mkdir_p(scripts)
    # Top-level scripts: ark.py shim, immutable_lib.py, mode.py, netmgr.py.
    # Open glob, sorted (1:1 with "$REPO_ROOT"/ark/scripts/*.py).
    for f in sorted((REPO_ROOT / "ark" / "scripts").glob("*.py")):
        if f.is_file():
            changed |= deploy_file(f, f"{scripts}/{f.name}", "644")
    # opslog (from lib/helpers/)
    changed |= deploy_file(REPO_ROOT / "lib/helpers/opslog.py", f"{scripts}/opslog.py")
    # Sub-packages: ark/, cask/, netmgr/
    for pkg in ("ark", "cask", "netmgr"):
        for f in sorted((REPO_ROOT / "ark" / "scripts" / pkg).rglob("*.py")):
            if not f.is_file():
                continue
            rel = f.relative_to(REPO_ROOT / "ark" / "scripts")
            _mkdir_p(f"{scripts}/{rel.parent}")
            changed |= deploy_file(f, f"{scripts}/{rel}")
    # immutable.sh wrapper + netns allowlist
    changed |= deploy_file(REPO_ROOT / "etc/ark/immutable/immutable.sh", f"{scripts}/immutable.sh", "755")
    changed |= deploy_file(REPO_ROOT / "etc/ark/netns-exec-allowlist.txt", f"{data_path}/netns-exec-allowlist.txt", "644")
    log_ok("Ark scripts deployed")
    return changed


def deploy_ark_domains(data_path: str) -> bool:
    """Deploy locked domain lists (640). Returns True if anything changed."""
    log_step("Deploying domain lists")
    changed = False
    _mkdir_p(f"{data_path}/domains/locked", f"{data_path}/domains/focused")
    for name in ("infra.txt", "base.txt", "session.txt", "deny.txt"):
        changed |= deploy_file(
            REPO_ROOT / "etc/ark/domains/locked" / name,
            f"{data_path}/domains/locked/{name}",
            "640",
        )
    log_ok("Domain lists deployed")
    return changed


def deploy_nftables(data_path: str) -> bool:
    """Deploy nftables base + staged copies. Returns True if changed."""
    log_step("Deploying nftables")
    changed = False
    changed |= deploy_file(REPO_ROOT / "etc/ark/nftables/nftables.conf.base", "/etc/nftables.conf")
    changed |= deploy_file(
        REPO_ROOT / "etc/ark/nftables/nftables.conf.base", f"{data_path}/nftables.conf.base", "640"
    )
    changed |= deploy_file(
        REPO_ROOT / "etc/ark/nftables/nftables.conf.restricted",
        f"{data_path}/nftables.conf.restricted",
        "640",
    )
    log_ok("nftables deployed")
    return changed


def deploy_polkit() -> bool:
    """Deploy polkit rules. Returns True if changed."""
    log_step("Deploying polkit rules")
    _mkdir_p("/etc/polkit-1/rules.d")
    changed = deploy_file(
        REPO_ROOT / "etc/ark/polkit/99-ark-polkit.rules",
        "/etc/polkit-1/rules.d/99-ark-polkit.rules",
    )
    log_ok("Polkit rules deployed")
    return changed


def deploy_resolv() -> bool:
    """Deploy netns resolv.conf. Returns True if changed."""
    log_step("Deploying netns resolv")
    _mkdir_p("/etc/netns/internet-netns")
    changed = deploy_file(
        REPO_ROOT / "etc/ark/resolv/internet-netns.resolv.conf",
        "/etc/netns/internet-netns/resolv.conf",
    )
    log_ok("Netns resolv.conf deployed")
    return changed


def deploy_systemd() -> bool:
    """Deploy systemd service + nftables override. Returns True if changed."""
    log_step("Deploying systemd service")
    changed = False
    changed |= deploy_file(
        REPO_ROOT / "etc/ark/systemd/internet-netns.service",
        "/etc/systemd/system/internet-netns.service",
    )
    _mkdir_p("/etc/systemd/system/nftables.service.d")
    changed |= deploy_file(
        REPO_ROOT / "etc/ark/systemd/nftables.service.d/override.conf",
        "/etc/systemd/system/nftables.service.d/override.conf",
    )
    log_ok("internet-netns.service + nftables override deployed")
    return changed


def deploy_sysctl() -> bool:
    """Deploy sysctl drop-in + apply live (warn-only). True if the file changed."""
    log_step("Deploying sysctl")
    _mkdir_p("/etc/sysctl.d")
    changed = deploy_file(
        REPO_ROOT / "etc/ark/sysctl.d/99-internet-netns.conf",
        "/etc/sysctl.d/99-internet-netns.conf",
    )
    # log_run mirror: each output line goes through log() (stdout is the
    # transcript install.py captures); the refresh itself never fails the run.
    rc, out = run_priv("sysctl", "--system")
    for line in out.splitlines():
        log(line)
    if rc != 0:
        log_warn("sysctl --system failed — forwarding may not be live")
    else:
        log_ok("ip_forward=1 enabled")
    return changed


def deploy_bin_scripts(bin_path: str) -> bool:
    """Remove retired bins + deploy mcask/uncask. True if a bin changed."""
    log_step("Deploying bin scripts")
    changed = False
    run_priv("rm", "-f", f"{bin_path}/cask-mobile")
    run_priv("rm", "-f", f"{bin_path}/lockdown")
    # One-time: remove the pre-rename polkit filename (renamed 99-ark-polkit).
    run_priv("rm", "-f", "/etc/polkit-1/rules.d/99-internet-lockdown.rules")
    changed |= deploy_file(REPO_ROOT / "ark/scripts/cask/mcask.py", f"{bin_path}/mcask", "755")
    changed |= deploy_file(REPO_ROOT / "ark/scripts/cask/uncask.py", f"{bin_path}/uncask", "755")
    log_ok(f"Bin scripts deployed to {bin_path}")
    return changed


def deploy_ark(bin_path: str) -> bool:
    """Deploy ark CLI + netmgr entry points. Returns True if changed."""
    log_step("Deploying ark tools")
    changed = False
    changed |= deploy_file(REPO_ROOT / "etc/ark/netmgr-bin.py", f"{bin_path}/netmgr", "755")
    changed |= deploy_file(REPO_ROOT / "ark/scripts/ark.py", f"{bin_path}/ark", "755")
    log_ok(f"Ark tools deployed to {bin_path}")
    return changed


def deploy_ark_perms(data_path: str) -> bool:
    """Harden ark data dirs + immutable flags (fail-closed).

    Returns True only for one-time transitions (mode bootstrap, seal→cask
    migration) — routine enforcement is noise, not change.
    """
    log_step("Setting ark permissions")
    changed = False
    # Raw chattr calls below are the deploy-time clear/set pattern. Exempt
    # from immutable_lib routing: repair_immutable (below) runs immediately
    # after and verifies + self-heals every flag, so raw calls are the fast
    # path with the module as the safety net.
    run_priv("chattr", "-i", f"{data_path}/mode")
    # Bootstrap /opt/ark/mode — create iff absent (root:root 0644; +i applied
    # below). An existing file is live state and is never touched or clobbered.
    # Bare test (no sudo): the parent dir is user-traversable here.
    if not os.path.isfile(f"{data_path}/mode"):
        _run_fatal(
            "sh", "-c",
            f"printf 'unrestricted\\n' | tee {shlex.quote(data_path + '/mode')} >/dev/null",
        )
        changed = True
    # Migration: old seal dir → cask (S9). One-time — runs only while /opt/ark/seal exists.
    if priv_exists("-d", f"{data_path}/seal") and not priv_exists("-e", f"{data_path}/cask"):
        run_priv(
            "chattr", "-i",
            f"{data_path}/seal/system.sealed",
            f"{data_path}/seal/mobile.sealed",
            f"{data_path}/seal/metadata.json",
        )
        _run_fatal("mv", f"{data_path}/seal", f"{data_path}/cask")
        # Rename casked files to the new-world names (inside the one-time guard).
        if priv_exists("-f", f"{data_path}/cask/system.sealed"):
            _run_fatal("mv", f"{data_path}/cask/system.sealed", f"{data_path}/cask/system.cask")
        if priv_exists("-f", f"{data_path}/cask/mobile.sealed"):
            _run_fatal("mv", f"{data_path}/cask/mobile.sealed", f"{data_path}/cask/mobile.cask")
        log(f"Migrated {data_path}/seal → {data_path}/cask")
        changed = True
    run_priv("chattr", "-i", f"{data_path}/cask/system.cask", f"{data_path}/cask/mobile.cask")
    _run_fatal("chown", "-R", "root:root", data_path)
    _run_fatal("chmod", "755", data_path)
    _mkdir_p(f"{data_path}/cask")
    rc, _ = run_priv("chown", "root:root", f"{data_path}/cask")
    if rc != 0:
        log_warn("cask chown failed")
    rc, _ = run_priv("chmod", "750", f"{data_path}/cask")
    if rc != 0:
        log_warn("cask chmod failed")
    _mkdir_p(f"{data_path}/logs")
    _run_fatal("chown", "root:root", f"{data_path}/logs")
    _run_fatal("chmod", "750", f"{data_path}/logs")
    # Raw chattr: deploy-time set (repair_immutable below self-heals).
    run_priv("chattr", "+i", f"{data_path}/mode")
    run_priv("chattr", "+i", f"{data_path}/cask/system.cask", f"{data_path}/cask/mobile.cask")

    # Self-heal any crash-interrupted cask/mode writes, then verify flags.
    # metadata.json is write-hot fallback metadata — atomic replace, no +i.
    immutable_lib = f"{data_path}/scripts/immutable_lib.py"
    rc, out = run_priv(
        "python3", immutable_lib, "repair",
        f"{data_path}/mode",
        f"{data_path}/cask/system.cask",
        f"{data_path}/cask/mobile.cask",
    )
    for line in out.splitlines():
        log(line)
    if rc != 0:
        log_error("immutable_lib repair reported failures — verifying below")

    imm_fail = False
    for f in (f"{data_path}/mode", f"{data_path}/cask/system.cask", f"{data_path}/cask/mobile.cask"):
        if priv_exists("-e", f):
            # Exact match: immutable_lib prints NOT-IMMUTABLE when the flag is
            # clear, and a substring test would match that too (60-ark.sh:453
            # could never fire). Fixed here — the .sh stays frozen.
            _, status = run_priv("python3", immutable_lib, "is", f)
            if status.strip() != "IMMUTABLE":
                log_error(f"Immutable flag missing on {f}")
                imm_fail = True
    if imm_fail:
        log_error("Immutable-flag verification failed — fix the flags, then re-run deploy")
        raise RuntimeError("immutable-flag verification failed")
    log_ok("Ark permissions set")
    return changed


def deploy_timeshift(data_path: str, helpers_dir: Path) -> bool:
    """Configure timeshift abort hook + excludes + state dir.

    True if the hook changed or timeshift.json content changed (hash diff —
    the injector exits 0 for both injected and noop); enforcement is noise.
    """
    log_step("Configuring timeshift for ark abort")

    # Abort state dir — the abort oracle (/opt/ark/state/enable.json) lives
    # here. 0750 root; NOT chattr +i'd (the restore-hook must be able to
    # delete enable.json, and the state dir is timeshift-excluded).
    _mkdir_p(f"{data_path}/state")
    _run_fatal("chown", "root:root", f"{data_path}/state")
    _run_fatal("chmod", "0750", f"{data_path}/state")

    # Restore-hook: run-parts requires a dotless name + exec bit. Written by
    # timeshift right before the forced reboot on an online restore; deletes
    # enable.json (breaks the re-abort reboot loop) + appends END abort: OK.
    _mkdir_p("/etc/timeshift/restore-hooks.d")
    changed = deploy_file(
        REPO_ROOT / "etc/ark/timeshift/99-ark-abort-log",
        "/etc/timeshift/restore-hooks.d/99-ark-abort-log",
        "755",
    )

    # Idempotently inject the ark excludes. /opt/ark/logs must survive a
    # restore (the hook writes its marker there); /opt/ark/state must survive
    # so the abort gate semantics stay deterministic post-restore.
    # Stdlib helper (no jq dependency — jq is not in the stage-0 set).
    tsjson = "/etc/timeshift/timeshift.json"
    rc_before, hash_before = run_priv("sha256sum", tsjson)
    rc, out = run_priv(
        "python3", str(helpers_dir / "timeshift_excludes.py"), tsjson,
        f"{data_path}/logs/***", f"{data_path}/state/***",
    )
    for line in out.splitlines():
        log(line)
    if rc != 0:
        log_error(f"timeshift exclude injection failed: {tsjson}")
        raise RuntimeError("timeshift exclude injection failed")
    _, hash_after = run_priv("sha256sum", tsjson)
    if rc_before != 0 or hash_before != hash_after:
        changed = True
    _run_fatal("chown", "root:root", tsjson)
    _run_fatal("chmod", "644", tsjson)
    log_ok(f"Timeshift excludes: {data_path}/logs/*** + {data_path}/state/***")
    return changed


def _result(status: str, changed: bool, message: str) -> None:
    safe = message.replace('"', "'").replace("\n", " ")
    print(f'RESULT {{"status": "{status}", "changed": {str(changed).lower()}, "message": "{safe}"}}')


def main(argv: list[str]) -> int:
    if {"-h", "--help"} & set(argv[1:]):
        print("Deploy ark system state: files + perms (offline, no network).")
        print("usage: python3 lib/60-ark-deploy.py")
        return 0
    if not require_user("lib/60-ark-deploy.py", "python3 lib/60-ark-deploy.py"):
        return 1
    try:
        paths = ark_common.env_paths()
        data_path = paths["ARK_DATA_PATH"]
        bin_path = paths["ARK_BIN_PATH"]
        lib_path = paths["ARK_LIB_PATH"]
        helpers_dir = REPO_ROOT / "lib" / "helpers"

        changed = False
        log_step("Ark deploy")
        check_retired_paths(data_path)
        backup_existing(data_path)
        changed |= deploy_adapters(lib_path)
        changed |= deploy_ark_scripts(data_path)
        changed |= deploy_ark_domains(data_path)
        changed |= deploy_nftables(data_path)
        changed |= deploy_polkit()
        changed |= deploy_resolv()
        changed |= deploy_systemd()
        changed |= deploy_sysctl()
        changed |= deploy_bin_scripts(bin_path)
        changed |= deploy_ark(bin_path)
        changed |= deploy_ark_perms(data_path)
        changed |= deploy_timeshift(data_path, helpers_dir)
    except Exception as exc:
        log_error(f"60-ark-deploy: {exc}")
        _result("FAIL", False, str(exc))
        return 1

    log_step("Ark deploy complete")
    _result("OK", changed, "ark deploy complete")
    print("60-ark-deploy: deployed")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
