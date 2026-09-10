#!/usr/bin/env python3
"""vm — staging VM harness for the linux_setup repo.

Drives a disposable QEMU/KVM replica via virsh: build the golden base image,
boot overlays, tear down, and check status. The golden image is a trixie
netinstall-equivalent built by lib/65-vm.sh (sudo required for that step).

Usage:
    vm --help             show this help and exit
    vm build [--force]    build the golden base image (runs lib/65-vm.sh via sudo)
    vm up                 resume the VM if defined, else boot a fresh overlay
    vm view [--yes]       open a virt-viewer window showing the VM's display
    vm shutdown [--force] gracefully power off the VM (ACPI), preserve the overlay
    vm destroy            tear down the running VM and delete the overlay
    vm status             show VM state, overlay, and snapshot info
    vm sync --branch X    sync the Path B clone to origin/X
    vm snapshot NAME      create an external disk snapshot (checkpoint)
    vm rollback NAME      roll back to a named snapshot

Provisioning is manual: `vm up` then `ssh vm` and run `./install.sh` inside
the VM (install.sh is interactive — sudo and reboot prompts). The harness
never invokes the orchestrator.

Deployed to ~/.local/bin/vm via the make dev tools loop. REPO_ROOT is read at
runtime from the deployed config.env copy (~/.config/linux_setup/config.env,
created by make dotfiles), so the script needs no deploy-time templating.
"""

from __future__ import annotations

import getpass
import grp
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

CONFIG_ENV = Path.home() / ".config" / "linux_setup" / "config.env"


def load_config_env(path: Path) -> dict[str, str]:
    """Parse a KEY=VALUE config file (comments and blank lines ignored)."""
    cfg: dict[str, str] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        cfg[key.strip()] = value.strip().strip('"').strip("'")
    return cfg


def get_repo_root() -> Path:
    """Resolve the repo root from the deployed config.env copy."""
    if not CONFIG_ENV.exists():
        print(
            "error: ~/.config/linux_setup/config.env not found.\n"
            "  Run 'make dotfiles' (or 'make all') to deploy it.",
            file=sys.stderr,
        )
        sys.exit(1)
    root = load_config_env(CONFIG_ENV).get("REPO_ROOT", "")
    if not root:
        print(
            "error: REPO_ROOT is not set in ~/.config/linux_setup/config.env.\n"
            "  Add REPO_ROOT=<repo path> to config.env and re-run 'make dotfiles'.",
            file=sys.stderr,
        )
        sys.exit(1)
    return Path(root)


REPO_ROOT = get_repo_root()
SCRIPT_DIR = REPO_ROOT / "vm"
TEMPLATE = SCRIPT_DIR / "domain.xml.template"
VM_BASE = Path("/var/lib/libvirt/vm")
GOLDEN_DIR = VM_BASE / "golden"
OVERLAY_DIR = VM_BASE / "overlays"
SSH_CONFIG = Path.home() / ".ssh" / "config"

VM_NAME = "staging-vm"
VM_USER = "vm"
VM_MEMORY = "4"
VM_VCPU = "2"
VIRTIOFS_DIR = str(REPO_ROOT)
MOUNT_TAG = "hostrepo"

# Overlay chain model — all-or-nothing:
#   golden.qcow2 -> staging-vm.qcow2 -> <snapshot>.qcow2 -> ... (active = top)
# `vm snapshot NAME` makes <name>.qcow2 the new active disk. The chain cannot be
# partially deleted: removing a middle snapshot breaks its children (backing
# file gone), and `vm up` always recreates staging-vm.qcow2, orphaning any kept
# snapshots. So `vm destroy` deletes the whole chain and there is deliberately
# no selection menu / selective snapshot deletion.
BOOT_TIMEOUT = 120
SHUTDOWN_TIMEOUT = 60
VM_REPO_PATH = "/home/vm/linux_setup"  # Path A: virtiofs ro mount of the host repo
VM_CLONE_PATH = "/home/vm/linux_setup-git"  # Path B: the VM's own git clone


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def run(cmd: list[str], *, check: bool = True, capture: bool = False, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    """Run a command, returning the CompletedProcess."""
    return subprocess.run(
        cmd,
        text=True,
        check=check,
        capture_output=capture,
        env=env,
    )


def sudo_run(cmd: list[str], *, check: bool = True, capture: bool = False) -> subprocess.CompletedProcess[str]:
    """Run a command via sudo (VM storage and libvirt are root-owned)."""
    return run(["sudo", *cmd], check=check, capture=capture)


def virsh(*args: str, check: bool = True) -> subprocess.CompletedProcess[str] | None:
    """Run virsh against the system connection via sudo.

    Returns None if the virsh binary is not installed (so callers can degrade
    gracefully before libvirt packages are installed). Never raises on a
    non-zero exit — the CompletedProcess is returned with its returncode so
    callers can inspect the failure.
    """
    if not shutil.which("virsh"):
        return None
    try:
        return sudo_run(["virsh", "-c", "qemu:///system", *args], check=check, capture=True)
    except subprocess.CalledProcessError as exc:
        return subprocess.CompletedProcess(exc.cmd, exc.returncode, exc.stdout, exc.stderr)


def virsh_fail(
    result: subprocess.CompletedProcess[str] | None,
    action: str,
    *,
    hint: str = "",
) -> bool:
    """Report a failed virsh call cleanly. Returns True if the call failed (caller returns 1)."""
    if result is None:
        print(f"error: {action} — virsh not installed (run 'make all')", file=sys.stderr)
        return True
    if result.returncode != 0:
        print(f"error: {action}", file=sys.stderr)
        if result.stderr:
            print(result.stderr.strip(), file=sys.stderr)
        if hint:
            print(f"  {hint}", file=sys.stderr)
        return True
    return False


def ssh_vm(cmd: str) -> subprocess.CompletedProcess[str]:
    """Run a command inside the VM via the ssh config entry."""
    return run(["ssh", "vm", cmd], check=False, capture=True)


def get_vm_ip() -> str:
    """Resolve the VM's IP address via virsh domifaddr."""
    result = virsh("domifaddr", VM_NAME, check=False)
    if result is None or result.returncode != 0:
        return ""
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 4 and parts[2].startswith("ipv4"):
            ip = parts[3].split("/")[0]
            if ip != "127.0.0.1":
                return ip
    return ""


def write_ssh_config(ip: str) -> None:
    """Write/update the Host vm entry in ~/.ssh/config."""
    SSH_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    marker_start = "# vm-managed-start"
    marker_end = "# vm-managed-end"
    entry = textwrap.dedent(f"""\
        {marker_start}
        Host vm
            HostName {ip}
            User {VM_USER}
            StrictHostKeyChecking no
            UserKnownHostsFile /dev/null
        {marker_end}
    """)

    lines: list[str] = []
    if SSH_CONFIG.exists():
        in_block = False
        for line in SSH_CONFIG.read_text().splitlines(keepends=True):
            if marker_start in line:
                in_block = True
                continue
            if marker_end in line:
                in_block = False
                continue
            if not in_block:
                lines.append(line)

    lines.append(entry)
    SSH_CONFIG.write_text("".join(lines))
    print(f"ssh config updated: Host vm -> {ip}")


def _get_active_disk() -> str:
    """Resolve the active disk path from the domain XML (or the canonical name)."""
    result = virsh("dumpxml", VM_NAME, check=False)
    if result is not None and result.returncode == 0:
        for line in result.stdout.splitlines():
            if "<source file=" in line:
                start = line.find("'")
                end = line.find("'", start + 1)
                if start != -1 and end != -1:
                    return line[start + 1 : end]
    return str(OVERLAY_DIR / f"{VM_NAME}.qcow2")


# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------


def cmd_build(args: list[str]) -> int:
    """Build the golden base image via lib/65-vm.sh (sudo required)."""
    build_script = REPO_ROOT / "lib" / "65-vm.sh"
    if not build_script.exists():
        print(f"error: {build_script} not found", file=sys.stderr)
        return 1

    force = "--force" in args

    # Check if golden already exists
    golden = GOLDEN_DIR / "golden.qcow2"
    if sudo_run(["test", "-f", str(golden)], check=False).returncode == 0 and not force:
        print(f"Golden base already exists: {golden}")
        resp = input("Rebuild? [y/N] ").strip().lower()
        if resp != "y":
            print("Aborted.")
            return 0
        force = True

    # The old golden stays in place (and overlays stay valid against it) until
    # the atomic swap in freeze_golden. Record its mtime so the swap can be
    # detected afterwards; only then are the now-invalid overlays dropped.
    before_mtime = ""
    if force:
        before = sudo_run(["stat", "-c", "%Y", str(golden)], check=False, capture=True)
        if before.returncode == 0:
            before_mtime = before.stdout.strip()

    sudo_run(["mkdir", "-p", str(GOLDEN_DIR)])
    cmd = ["sudo", "bash", str(build_script)]
    if force:
        cmd.append("--force")
    result = run(cmd, check=False)

    if force:
        after = sudo_run(["stat", "-c", "%Y", str(golden)], check=False, capture=True)
        after_mtime = after.stdout.strip() if after.returncode == 0 else ""
        if before_mtime and after_mtime and before_mtime != after_mtime:
            # Golden swapped: every old overlay/snapshot references it by path
            # and is now invalid. Tear the VM down and drop the overlay chain.
            print("Golden replaced — removing overlays/snapshots that referenced the old golden.")
            rc = cmd_destroy(["--yes"])
            if rc != 0:
                return rc

    return result.returncode


def _wait_and_configure_ssh() -> None:
    """Wait for the VM to boot and write ssh config."""
    ip = ""
    for _ in range(0, BOOT_TIMEOUT, 5):
        ip = get_vm_ip()
        if ip:
            break
        subprocess.run(["sleep", "5"], check=False)

    if ip:
        write_ssh_config(ip)
    else:
        print("warning: could not resolve VM IP (ssh may not work)")

    print("Use 'ssh vm' or 'vm view' to see the desktop")


def _define_and_start(disk_path: Path) -> int:
    """Render the domain XML for disk_path, define, start, and configure ssh."""
    if not TEMPLATE.exists():
        print(f"error: domain template not found: {TEMPLATE}", file=sys.stderr)
        return 1

    template_text = TEMPLATE.read_text()
    domain_xml = template_text.format(
        vm_name=VM_NAME,
        memory=VM_MEMORY,
        vcpu=VM_VCPU,
        disk_path=str(disk_path),
        virtiofs_dir=VIRTIOFS_DIR,
        mount_tag=MOUNT_TAG,
    )

    xml_path = OVERLAY_DIR / "domain.xml"
    with tempfile.NamedTemporaryFile("w", suffix=".xml", delete=False) as f:
        f.write(domain_xml)
        tmp_xml = f.name
    sudo_run(["cp", tmp_xml, str(xml_path)])
    Path(tmp_xml).unlink(missing_ok=True)

    result = virsh("define", str(xml_path), check=False)
    if virsh_fail(result, "failed to define domain"):
        return 1

    result = virsh("start", VM_NAME, check=False)
    if virsh_fail(result, f"failed to start VM '{VM_NAME}'"):
        return 1
    print(f"VM '{VM_NAME}' started")

    _wait_and_configure_ssh()
    return 0


def _create_fresh_overlay() -> int:
    """Create overlay from golden, define, and start."""
    golden = GOLDEN_DIR / "golden.qcow2"
    if sudo_run(["test", "-f", str(golden)], check=False).returncode != 0:
        print("error: golden base not found. Run 'vm build' first.", file=sys.stderr)
        return 1

    sudo_run(["mkdir", "-p", str(OVERLAY_DIR)])
    overlay = OVERLAY_DIR / f"{VM_NAME}.qcow2"

    sudo_run([
        "qemu-img", "create", "-f", "qcow2",
        "-b", str(golden), "-F", "qcow2", str(overlay),
    ])
    sudo_run(["chown", "libvirt-qemu:libvirt-qemu", str(overlay)])

    return _define_and_start(overlay)


def cmd_up(_args: list[str]) -> int:
    """Boot the VM — resume if defined, create fresh from golden if not."""
    result = virsh("domstate", VM_NAME, check=False)
    if result is not None and result.returncode == 0:
        state = result.stdout.strip()
        if state == "running":
            print(f"VM '{VM_NAME}' is already running")
            return 0
        active = _get_active_disk()
        if sudo_run(["test", "-f", active], check=False).returncode == 0:
            result = virsh("start", VM_NAME, check=False)
            if virsh_fail(result, f"failed to start VM '{VM_NAME}'", hint="Try 'vm destroy' then 'vm up' for a fresh overlay."):
                return 1
            print(f"VM '{VM_NAME}' resumed")
            _wait_and_configure_ssh()
            return 0
        print(f"Stale domain: disk {active} is missing. Recreating from golden...")
        result = virsh("undefine", VM_NAME, "--snapshots-metadata", "--nvram", check=False)
        if virsh_fail(result, f"failed to undefine stale VM '{VM_NAME}'", hint="Run 'vm destroy' to clean up."):
            return 1

    overlay = OVERLAY_DIR / f"{VM_NAME}.qcow2"
    if sudo_run(["test", "-f", str(overlay)], check=False).returncode == 0:
        print(f"Orphaned overlay found: {overlay}")
        print("  Cleaning up and creating fresh overlay from golden...")
        sudo_run(["rm", "-f", str(overlay)])

    return _create_fresh_overlay()


def cmd_shutdown(args: list[str]) -> int:
    """Gracefully power off the VM (ACPI), preserving the overlay chain."""
    force = "--force" in args

    result = virsh("domstate", VM_NAME, check=False)
    if virsh_fail(result, "VM is not defined. Run 'vm up' first."):
        return 1
    assert result is not None
    state = result.stdout.strip()
    if state == "shut off":
        print(f"VM '{VM_NAME}' is already shut off")
        return 0

    result = virsh("shutdown", VM_NAME, check=False)
    if virsh_fail(result, f"failed to send ACPI shutdown to VM '{VM_NAME}'"):
        return 1
    print(f"VM '{VM_NAME}' shutting down (ACPI)...")

    for _ in range(0, SHUTDOWN_TIMEOUT, 5):
        state_result = virsh("domstate", VM_NAME, check=False)
        if state_result is not None and state_result.returncode == 0:
            if state_result.stdout.strip() == "shut off":
                print(f"VM '{VM_NAME}' shut off (overlay preserved — 'vm up' resumes it)")
                return 0
        subprocess.run(["sleep", "5"], check=False)

    if force:
        result = virsh("destroy", VM_NAME, check=False)
        if virsh_fail(result, f"failed to force power-off VM '{VM_NAME}'"):
            return 1
        print(f"VM '{VM_NAME}' force-powered off (overlay preserved — 'vm up' resumes it)")
        return 0

    print(f"error: VM '{VM_NAME}' did not shut down within {SHUTDOWN_TIMEOUT}s", file=sys.stderr)
    print("  Use 'vm shutdown --force' to force power-off.", file=sys.stderr)
    return 1


def libvirt_access_hint() -> str:
    """State-aware hint for a failed qemu:///system connection."""
    try:
        entry = grp.getgrnam("libvirt")
    except KeyError:
        return "  Hint: the 'libvirt' group does not exist — install libvirt (run 'make all')."
    user = getpass.getuser()
    in_group = user in entry.gr_mem or os.getgid() == entry.gr_gid
    in_session = os.getgid() == entry.gr_gid or entry.gr_gid in os.getgroups()
    if not in_group:
        return (
            "  Hint: add yourself to the libvirt group for unprivileged access:\n"
            "    sudo usermod -aG libvirt $USER\n"
            "    Then log out and back in."
        )
    if not in_session:
        return (
            "  Hint: you're in the libvirt group, but this session predates the change.\n"
            "    Log out and back in (or run: newgrp libvirt) for it to take effect."
        )
    return "  Hint: the libvirt connection failed for another reason — see the error above."


def cmd_view(args: list[str]) -> int:
    """Open a virt-viewer window showing the VM's display (SPICE)."""
    if not shutil.which("virt-viewer"):
        print("error: virt-viewer not installed (run 'make all')", file=sys.stderr)
        return 1

    # Guard: a second virt-viewer on the same SPICE port cannot stay open
    # (the SPICE server is single-client). Inform and exit — never kill the
    # existing window. --yes forces the launch attempt anyway.
    existing = run(["pgrep", "-f", f"virt-viewer.*{VM_NAME}"], check=False, capture=True)
    if existing.returncode == 0:
        pids = existing.stdout.strip().split()
        print(f"virt-viewer is already connected to '{VM_NAME}' (PID {', '.join(pids)})")
        print("Only one SPICE window is supported per VM — the existing window is showing the display.")
        if "--yes" not in args and "-y" not in args:
            return 0

    # Auto-start the VM if not running (same pattern as cmd_sync)
    rc = cmd_up([])
    if rc != 0:
        return rc

    # Force the dark GTK theme: virt-viewer otherwise renders its window
    # chrome light (white text on white) despite the host's dark theme.
    env = {**os.environ, "GTK_THEME": "Adwaita:dark"}
    result = run(["virt-viewer", "--connect", "qemu:///system", VM_NAME], env=env, check=False)
    if result.returncode != 0:
        print("error: virt-viewer failed to connect", file=sys.stderr)
        if result.stderr:
            print(result.stderr.strip(), file=sys.stderr)
        print(libvirt_access_hint(), file=sys.stderr)
    return result.returncode


def cmd_destroy(args: list[str]) -> int:
    """Tear down the VM and delete all overlays (requires --yes to skip confirmation)."""
    if "--yes" not in args and "-y" not in args:
        print("This will destroy the VM and ALL overlays:")
        result = virsh("snapshot-list", VM_NAME, "--name", check=False)
        if result is not None and result.returncode == 0 and result.stdout.split():
            print("  Snapshots:")
            for snap in result.stdout.split():
                print(f"    - {snap}")
        print(f"  Overlays: {OVERLAY_DIR}/staging-vm* and *.qcow2")
        print("  Domain definition + NVRAM")
        try:
            confirmed = input("Destroy the VM and all overlays? [y/N] ").strip().lower()
        except EOFError:
            confirmed = ""
        if confirmed not in ("y", "yes"):
            print("Aborted.")
            return 1

    # Stop if running
    result = virsh("domstate", VM_NAME, check=False)
    if result is None:
        print("  (virsh not installed — run 'make all' to install libvirt)")
    elif result.returncode == 0:
        state = result.stdout.strip()
        if state != "shut off":
            virsh("destroy", VM_NAME, check=False)
        result = virsh("undefine", VM_NAME, "--snapshots-metadata", "--nvram", check=False)
        if virsh_fail(result, f"failed to undefine VM '{VM_NAME}'"):
            return 1
        print(f"VM '{VM_NAME}' destroyed")
    else:
        print(f"VM '{VM_NAME}' is not defined")

    # Delete the whole chain, not just the active overlay: snapshots share the
    # backing chain, so keeping any while vm up recreates staging-vm.qcow2
    # would orphan them (see module comment on the chain model).
    sudo_run(["bash", "-c", f"rm -f {OVERLAY_DIR}/staging-vm* {OVERLAY_DIR}/*.qcow2"])
    print(f"Overlays removed: {OVERLAY_DIR}")

    # Remove domain xml
    xml_path = OVERLAY_DIR / "domain.xml"
    if sudo_run(["test", "-f", str(xml_path)], check=False).returncode == 0:
        sudo_run(["rm", "-f", str(xml_path)])

    return 0


def cmd_status(_args: list[str]) -> int:
    """Show VM state, overlay, and snapshot info."""
    print("=== Golden Base ===")
    golden = GOLDEN_DIR / "golden.qcow2"
    if sudo_run(["test", "-f", str(golden)], check=False).returncode == 0:
        size = int(sudo_run(["stat", "-c", "%s", str(golden)], check=False, capture=True).stdout.strip())
        print(f"  {golden} ({size / (1024**3):.1f} GiB)")
    else:
        print("  (not built)")

    print("\n=== Overlay ===")
    active = _get_active_disk()
    if sudo_run(["test", "-f", active], check=False).returncode == 0:
        size = int(sudo_run(["stat", "-c", "%s", active], check=False, capture=True).stdout.strip())
        print(f"  {active} ({size / (1024**2):.1f} MiB)")
    else:
        print("  (none)")

    print("\n=== Snapshots ===")
    result = virsh("snapshot-list", VM_NAME, check=False)
    if result is None:
        print("  (virsh not installed — run 'make all' to install libvirt)")
    elif result.returncode == 0:
        print(result.stdout)
    else:
        print("  (none)")

    print("\n=== Libvirt ===")
    result = virsh("list", "--all", check=False)
    if result is None:
        print("  (virsh not installed — run 'make all' to install libvirt)")
    elif result.returncode == 0:
        print(result.stdout)
    else:
        print("  (virsh not available or libvirtd not running)")

    print("=== SSH Config ===")
    if SSH_CONFIG.exists():
        content = SSH_CONFIG.read_text()
        if "vm-managed-start" in content:
            print("  Host vm entry: present")
        else:
            print("  Host vm entry: not found (run 'vm up')")
    else:
        print("  ~/.ssh/config: not found")

    return 0


def cmd_sync(args: list[str]) -> int:
    """Sync the Path B clone to a branch tip."""
    branch = ""
    if "--branch" in args:
        i = args.index("--branch")
        if i + 1 < len(args):
            branch = args[i + 1]
    if not branch:
        print("usage: vm sync --branch X", file=sys.stderr)
        return 1

    rc = cmd_up([])
    if rc != 0:
        return rc

    result = ssh_vm(f"test -d {VM_CLONE_PATH}/.git || git clone {VM_REPO_PATH} {VM_CLONE_PATH}")
    if result.returncode != 0:
        print(f"error: could not ensure clone at {VM_CLONE_PATH}", file=sys.stderr)
        print(result.stdout, file=sys.stderr)
        print(result.stderr, file=sys.stderr)
        return 1

    result = ssh_vm(f"cd {VM_CLONE_PATH} && git fetch origin && git reset --hard origin/{branch}")
    if result.returncode != 0:
        print(f"error: sync to origin/{branch} failed", file=sys.stderr)
        print(result.stdout, file=sys.stderr)
        print(result.stderr, file=sys.stderr)
        return 1
    print(f"synced {VM_CLONE_PATH} to origin/{branch}")
    return 0


def cmd_snapshot(args: list[str]) -> int:
    """Create an external disk snapshot (checkpoint) of the VM."""
    if len(args) != 1:
        print("usage: vm snapshot NAME", file=sys.stderr)
        return 1
    name = args[0]
    if name in ("staging-vm", "golden") or "/" in name or ".." in name:
        print(f"error: invalid snapshot name '{name}'", file=sys.stderr)
        return 1

    result = virsh("domstate", VM_NAME, check=False)
    if virsh_fail(result, "VM is not defined. Run 'vm up' first."):
        return 1

    # The snapshot overlay becomes the new active disk (top of the chain).
    result = virsh(
        "snapshot-create-as", VM_NAME, name, "--disk-only",
        "--diskspec", f"vda,snapshot=external,file={OVERLAY_DIR}/{name}.qcow2",
        "--atomic", check=False,
    )
    if virsh_fail(result, f"snapshot '{name}' failed"):
        return 1
    print(f"snapshot '{name}' created")
    return 0


def cmd_rollback(args: list[str]) -> int:
    """Roll back to a named snapshot (fresh overlay on the checkpoint)."""
    if len(args) != 1:
        print("usage: vm rollback NAME", file=sys.stderr)
        return 1
    name = args[0]

    result = virsh("snapshot-list", VM_NAME, "--name", check=False)
    if virsh_fail(result, "VM is not defined. Run 'vm up' first."):
        return 1
    assert result is not None
    if name not in result.stdout.split():
        print(f"error: snapshot '{name}' not found", file=sys.stderr)
        return 1

    active = _get_active_disk()
    checkpoint = OVERLAY_DIR / f"{name}.qcow2"

    # Tear down (keep the checkpoint and its backing chain)
    state_result = virsh("domstate", VM_NAME, check=False)
    if state_result is not None and state_result.returncode == 0:
        state = state_result.stdout.strip()
        if state != "shut off":
            virsh("destroy", VM_NAME, check=False)
        virsh("snapshot-delete", VM_NAME, name, "--metadata", check=False)
        result = virsh("undefine", VM_NAME, "--nvram", check=False)
        if virsh_fail(result, f"failed to undefine VM '{VM_NAME}'"):
            return 1

    # Delete the top-of-chain active disk (but not the checkpoint)
    if active != str(checkpoint):
        sudo_run(["rm", "-f", active])

    # Fresh overlay on the checkpoint
    overlay = OVERLAY_DIR / f"{VM_NAME}.rollback.qcow2"
    sudo_run(["rm", "-f", str(overlay)])
    sudo_run([
        "qemu-img", "create", "-f", "qcow2",
        "-b", str(checkpoint), "-F", "qcow2", str(overlay),
    ])
    sudo_run(["chown", "libvirt-qemu:libvirt-qemu", str(overlay)])

    return _define_and_start(overlay)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

USAGE = """usage: vm <command> [args]

Commands:
    build [--force]    build the golden base image (runs lib/65-vm.sh via sudo)
    up                 resume the VM if defined, else boot a fresh overlay
    view [--yes]        open a virt-viewer window showing the VM's display
    shutdown [--force]  gracefully power off the VM (ACPI), preserve the overlay
    destroy [--yes]     tear down the VM and delete all overlays (confirm required)
    status             show VM state, overlay, and snapshot info
    sync --branch X    sync the Path B clone to origin/X
    snapshot NAME      create an external disk snapshot (checkpoint)
    rollback NAME      roll back to a named snapshot
"""

COMMANDS = {
    "build": cmd_build,
    "up": cmd_up,
    "view": cmd_view,
    "shutdown": cmd_shutdown,
    "destroy": cmd_destroy,
    "status": cmd_status,
    "sync": cmd_sync,
    "snapshot": cmd_snapshot,
    "rollback": cmd_rollback,
}


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(USAGE, file=sys.stderr)
        return 1
    if argv[1] in ("-h", "--help"):
        print(USAGE)
        return 0
    if argv[1] not in COMMANDS:
        print(USAGE, file=sys.stderr)
        print(f"error: unknown command '{argv[1]}'", file=sys.stderr)
        return 1

    command = argv[1]
    return COMMANDS[command](argv[2:])


if __name__ == "__main__":
    sys.exit(main(sys.argv))
