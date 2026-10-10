#!/usr/bin/env python3
"""Ark policy pipeline: rendered policy + live actuation (policy half of 60-ark.sh).

Ports deploy_sudoers → subst_templates → deploy_system_dns →
deploy_blocklist → deploy_browser_policies → validate_configs →
reload_services (60-ark.sh:622-644 relative order). Static file + state
deploy lives in 60-ark-deploy.py.

Runs as your normal user and escalates internally via sudo (never run
this module itself as root). The `sources download` step needs network
(install.py wires needs_net=True, timeout=900 — not module code).

Run standalone: python3 lib/61-ark-policy.py
Wired into install.py as 61-ark-policy

Exit codes:
    0  policy deployed
    1  failure (fail-closed; False/empty never means failure)
Stdlib only.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
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
    run_priv,
)
from preconditions import require_user  # noqa: E402  (refuse-root guard)

# Absolute path: bash `:559` relies on bare `netmgr` resolving via sudo's
# secure_path. Pinned here (ported hardening — the .sh stays frozen).
NETMGR_BIN = "/usr/local/bin/netmgr"


def _run_fatal(*args: str) -> str:
    """run_priv that raises on nonzero (bash set -e parity). Returns output."""
    rc, out = run_priv(*args)
    if rc != 0:
        raise RuntimeError(f"{' '.join(args)} failed (rc={rc}): {out.strip()}")
    return out


def _mkdir_p(*dirs: str) -> None:
    _run_fatal("mkdir", "-p", *dirs)


def deploy_sudoers() -> bool:
    """Render, validate, and deploy sudoers policy. True if a file changed."""
    log_step("Deploying sudoers")

    # Phase 0 — manifest pre-flight: SUDOERS_FILES must match the source
    # dir exactly. A missing or unlisted file is a broken checkout — abort
    # before anything is staged.
    for f in ark_common.SUDOERS_FILES:
        src = REPO_ROOT / "etc/ark/sudoers.d" / f
        if not src.is_file():
            log_error(f"Sudoers source missing in repo: {src} (broken checkout?)")
            raise RuntimeError(f"sudoers source missing: {f}")
    for src in sorted((REPO_ROOT / "etc/ark/sudoers.d").iterdir()):
        if not src.is_file():
            continue
        if src.name not in ark_common.SUDOERS_FILES:
            log_error(
                f"Sudoers source not in manifest: {src.name}"
                " — add it to SUDOERS_FILES or remove it"
            )
            raise RuntimeError(f"sudoers source not in manifest: {src.name}")

    rc, _ = run_priv("mkdir", "-p", "/etc/sudoers.d")
    if rc != 0:
        log_error("Failed to create /etc/sudoers.d")
        raise RuntimeError("cannot create /etc/sudoers.d")

    # Phase 1 — render each source to staging. The stage is user-owned
    # (mkdtemp) and gomplate runs UNSUDO'D, exactly like the bare
    # `gomplate` at 60-ark.sh:187: sudo'ing the render would leave
    # root-owned staged files the combine step below cannot read.
    # Stderr is captured, not discarded: after Phase 0, failure here can
    # only mean a genuine template/env problem, and the message must say
    # which.
    try:
        stage = tempfile.mkdtemp(prefix="ark-sudoers-")
    except OSError as exc:
        log_error("Failed to create sudoers staging dir")
        raise RuntimeError(f"cannot create sudoers staging dir: {exc}") from exc
    try:
        for f in ark_common.SUDOERS_FILES:
            proc = subprocess.run(
                [
                    "gomplate", "--missing-key", "error",
                    "-f", str(REPO_ROOT / "etc/ark/sudoers.d" / f),
                    "-o", f"{stage}/{f}",
                ],
                capture_output=True,
                text=True,
            )
            if proc.returncode != 0:
                log_error(f"Sudoers render failed: {f}")
                log_error(((proc.stdout or "") + (proc.stderr or "")).strip())
                raise RuntimeError(f"sudoers render failed: {f}")

        # Phase 2 — combine with file-boundary markers (legal sudoers
        # comments) so visudo line numbers stay attributable to a source
        # file. Byte-identical to the bash loop (marker + raw bytes + one
        # newline per file).
        combined_text = ""
        for f in ark_common.SUDOERS_FILES:
            combined_text += f"# --- {f} ---\n" + Path(f"{stage}/{f}").read_text() + "\n"
        Path(f"{stage}/combined").write_text(combined_text)

        # Phase 3 — validate the staged artifact. Combined-file check (not
        # per-file): themed files reference aliases defined in 00-base, so
        # a lone per-file visudo would false-fail. Abort deploys nothing.
        visudo = ark_common.find_visudo()
        if visudo is None:
            raise RuntimeError("visudo not found (checked PATH and /usr/sbin/visudo)")
        rc, out = run_priv(visudo, "-c", "-f", f"{stage}/combined")
        if rc != 0:
            log_error("Sudoers validation failed — deploying nothing")
            err = ark_common.parse_visudo_error(out.strip(), combined_text)
            log_error(err.raw)
            if err.line is not None:
                log_error(f"Offending file: {err.owner} (combined line {err.line})")
                log_error("Context:")
                for line in err.context:
                    log_error(f"  {line}")
            raise RuntimeError("sudoers validation failed")

        # Phase 4 — deploy the VALIDATED artifact (not the raw sources), so
        # the validated bytes are byte-identical to what goes live. The
        # later subst_templates pass finds no markers here and skips these
        # files.
        changed = False
        for f in ark_common.SUDOERS_FILES:
            try:
                changed |= deploy_file(f"{stage}/{f}", f"/etc/sudoers.d/{f}", "440")
            except RuntimeError:
                log_error(f"Sudoers deploy failed: {f}")
                raise
    finally:
        shutil.rmtree(stage, ignore_errors=True)

    # Phase 5 — whole-policy confirmation. Staged content passed seconds
    # earlier, so failure here means live state drifted (concurrent edit),
    # not a source error.
    visudo = ark_common.find_visudo()
    if visudo is None:
        raise RuntimeError("visudo not found (checked PATH and /usr/sbin/visudo)")
    rc, out = run_priv(visudo, "-c")
    if rc != 0:
        log_error("Live sudoers policy invalid after deploy — staged content passed,")
        log_error("so live state drifted (concurrent edit?). Inspect before re-running.")
        log_error(out.strip())
        raise RuntimeError("live sudoers policy invalid")
    log_ok("Sudoers deployed")
    return changed


def subst_templates(render_paths: list[str]) -> bool:
    """Render live templates via render_templates.py. Always False (sync, not state)."""
    log_step("Template substitution (gomplate)")

    # render_templates.py attempts every template, isolates per-file
    # gomplate failures (temp-file render + atomic replace — a failed file
    # keeps its raw {{ .Env.* }} markers and is never corrupted), and prints
    # a full report. Exit 0 = all rendered; 1 = some failed (report in
    # output); anything else = the renderer itself crashed. Any failure
    # aborts the deploy — the post-deploy steps below import these files
    # and must never run against un-rendered templates.
    rc, out = run_priv(
        "python3", str(REPO_ROOT / "lib" / "helpers" / "render_templates.py"), *render_paths
    )
    if rc != 0:
        if rc == 1:
            log_error("Template substitution failed:")
        else:
            log_error(f"render_templates.py crashed (exit {rc}):")
        log_error(out.strip())
        raise RuntimeError("template substitution failed")
    log_ok(out.strip())
    return False


def deploy_system_dns(data_path: str) -> bool:
    """Live DNS actuation via netmgr. Always False (enforcement, not state)."""
    log_step("System DNS (netmgr)")
    netmgr = f"{data_path}/scripts/netmgr.py"
    _run_fatal("python3", netmgr, "system", "setup-dns")
    _run_fatal("python3", netmgr, "system", "setup-podman-dns")
    # Exec-grant deploy — builds thin shims + inet (dispatch grants removed).
    # Runs after subst_templates (renders wrappers.py before it's imported).
    _run_fatal("python3", netmgr, "exec-grant", "deploy")
    log_ok("System DNS configured")
    return False


def deploy_blocklist(data_path: str) -> bool:
    """Deploy repo blocklist files + download upstream sources.

    True if a repo file changed. The `sources download` refresh is
    deliberately non-idempotent but excluded from changed (cache, not
    deployed state — the blocklist only changes on `netmgr generate`).
    """
    log_step("Deploying blocklist")

    src_dir = REPO_ROOT / "etc/ark/domains/focused"
    dst_dir = f"{data_path}/domains/focused"
    _mkdir_p(dst_dir)

    # Deploy focused/domain files — always from repo (repo is the source of
    # truth; live is a deploy artifact). netmgr mutations are repo-first
    # (write repo + sync live), so overwriting is a no-op when in sync. Live
    # copies are root:root — custom 640, others 644 — so non-root users can't
    # modify them. A missing repo file is a broken checkout — abort, don't
    # silently keep a stale live copy.
    changed = False
    for name in ark_common.BLOCKLIST_FILES:
        if (src_dir / name).is_file():
            changed |= deploy_file(src_dir / name, f"{dst_dir}/{name}", ark_common.blocklist_mode(name))
            log(f"Deployed {name} from repo")
        else:
            log_error(f"{name} missing in repo")
            raise RuntimeError(f"blocklist source missing: {name}")

    # Download all enabled sources — download only, never auto-generate. The
    # final blocklist is curated + generated by the user (netmgr generate) so
    # ark enable's fail-closed preflight (check_blocklist_dnsmasq) stays
    # meaningful: a forgotten generate aborts enable instead of locking with an
    # uncurated list.
    #
    # Deliberately NOT idempotent: install.py runs infrequently while upstream
    # lists (StevenBlack, blocklistproject, ...) are refreshed near-daily, so
    # re-downloading on every run guarantees the latest lists are pulled — the
    # one intentional exception to the repo's idempotent deploy model. It is
    # fail-tolerant: a failed fetch keeps the previous upstream file, and since
    # we never auto-generate, the deployed blocklist only changes when the user
    # runs `netmgr generate`.
    log("Running netmgr sources download...")
    rc, _ = run_priv(NETMGR_BIN, "sources", "download")
    if rc != 0:
        log_error("netmgr sources download failed")
        raise RuntimeError("netmgr sources download failed")
    log_ok("Sources downloaded (run 'netmgr generate' to build the blocklist)")
    return changed


def deploy_browser_policies(data_path: str) -> bool:
    """Stage browser policy templates + generate live policies.

    True if a staged template changed; the generation actuation is excluded.
    """
    log_step("Browser policies")
    changed = False
    for src_rel, staged, mode in ark_common.BROWSER_SOURCES:
        changed |= deploy_file(REPO_ROOT / src_rel, f"{data_path}/{staged}", mode)
    log("Generating browser policies...")
    _run_fatal("python3", f"{data_path}/scripts/netmgr.py", "deploy-policies")
    log_ok("Browser policies deployed")
    return changed


def validate_configs(data_path: str) -> bool:
    """Validate sudoers + rendered configs. Always False (pure validation)."""
    log_step("Validating configs")
    # PATH-proof absolute visudo unifies 60-ark.sh:212 (bare) and :247/:573
    # (sudo secure_path) — ported fix, the .sh stays frozen.
    visudo = ark_common.find_visudo()
    if visudo is None:
        raise RuntimeError("visudo not found (checked PATH and /usr/sbin/visudo)")
    rc, out = run_priv(visudo, "-c")
    if rc != 0:
        log_error("Sudoers validation failed")
        log_error(out.strip())
        raise RuntimeError("sudoers validation failed")
    log_ok("Sudoers valid")

    # Bare test correct: focused/ chain is 755 with 644 files (user-readable).
    netmgr = f"{data_path}/scripts/netmgr.py"
    if os.path.isfile(f"{data_path}/domains/focused/blocklist.dnsmasq.conf"):
        log("Validating rendered configs (nft -c -f + dnsmasq --test)...")
        mode = "focused"
    else:
        log("Blocklist not generated yet — run 'netmgr generate' before ark enable")
        log("Validating base config (nft -c -f + dnsmasq --test, unrestricted)...")
        mode = "unrestricted"
    rc, _ = run_priv("python3", netmgr, "validate", mode)
    if rc != 0:
        log_error("Config validation failed")
        raise RuntimeError("config validation failed")
    log_ok("Configs valid")
    return False


def reload_services() -> bool:
    """Reload daemons + enable services. Always False (actuation, not state)."""
    log_step("Reloading services")
    _run_fatal("systemctl", "daemon-reload")
    # Clear any stale regular-file entry in the wants dir — systemctl enable
    # only manages symlinks and fails with "File ... already exists" if a
    # leftover copy (e.g. from a manual test) sits there.
    run_priv("rm", "-f", "/etc/systemd/system/multi-user.target.wants/internet-netns.service")
    _run_fatal("systemctl", "enable", "--now", "internet-netns.service")
    _run_fatal("systemctl", "enable", "--now", "dnsmasq")
    # Warn-only restart (log_run mirror: output through log()); failure must
    # not fail the run — same class as deploy_sysctl's sysctl --system.
    rc, out = run_priv("systemctl", "restart", "nftables")
    for line in out.splitlines():
        log(line)
    if rc != 0:
        log_warn("nftables restart failed — firewall rules may not be live")
    else:
        log_ok("Services reloaded")
    return False


def _result(status: str, changed: bool, message: str) -> None:
    safe = message.replace('"', "'").replace("\n", " ")
    print(f'RESULT {{"status": "{status}", "changed": {str(changed).lower()}, "message": "{safe}"}}')


def main(argv: list[str]) -> int:
    if {"-h", "--help"} & set(argv[1:]):
        print("Deploy ark policy: rendered policy + live actuation (needs network).")
        print("usage: python3 lib/61-ark-policy.py")
        return 0
    if not require_user("lib/61-ark-policy.py", "python3 lib/61-ark-policy.py"):
        return 1
    try:
        paths = ark_common.env_paths()
        data_path = paths["ARK_DATA_PATH"]
        render_paths = paths["ARK_RENDER_PATHS"].split()

        # Standalone pre-flight: F3-F6 exec netmgr.py, deployed by the
        # 60-module. Fail loud here instead of mid-run.
        netmgr = f"{data_path}/scripts/netmgr.py"
        if not os.path.isfile(netmgr):
            log_error(f"netmgr.py missing: {netmgr} (run 60-ark-deploy first)")
            raise RuntimeError(f"netmgr.py missing: {netmgr}")

        changed = False
        log_step("Ark policy")
        changed |= deploy_sudoers()
        changed |= subst_templates(render_paths)
        changed |= deploy_system_dns(data_path)
        changed |= deploy_blocklist(data_path)
        changed |= deploy_browser_policies(data_path)
        changed |= validate_configs(data_path)
        changed |= reload_services()
    except Exception as exc:
        log_error(f"61-ark-policy: {exc}")
        _result("FAIL", False, str(exc))
        return 1

    log_step("Ark policy complete")
    _result("OK", changed, "ark policy complete")
    print("61-ark-policy: deployed")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
