#!/usr/bin/env python3
"""vm — staging VM harness for the linux_setup repo.

Drives a disposable QEMU/KVM replica via virsh: build the golden base image,
boot overlays, tear down, and check status. The golden image is a trixie
netinstall-equivalent built by lib/65-vm.sh (sudo required for that step).

Usage:
    vm build [--force]    build the golden base image (runs lib/65-vm.sh via sudo)
    vm up                 boot a fresh overlay from golden
    vm destroy            tear down the running VM and delete the overlay
    vm status             show VM state, overlay, and snapshot info

Deployed to ~/.local/bin/vm via the make dev tools loop; REPO_ROOT is baked
in by the repo's gomplate renderer (install.sh runs render_templates.py on
~/.local/bin after make-all). The repo copy keeps the raw marker and is not
meant to be run directly.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

REPO_ROOT = Path("{{ .Env.REPO_ROOT }}")
if not REPO_ROOT.exists():
    print(
        "error: vm.py is an unrendered gomplate template (raw REPO_ROOT marker).\n"
        "  Run install.sh (or: make dev && python3 lib/render_templates.py ~/.local/bin)",
        file=sys.stderr,
    )
    sys.exit(1)
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
BOOT_TIMEOUT = 120


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def run(cmd: list[str], *, check: bool = True, capture: bool = False) -> subprocess.CompletedProcess[str]:
    """Run a command, returning the CompletedProcess."""
    return subprocess.run(
        cmd,
        text=True,
        check=check,
        capture_output=capture,
    )


def sudo_run(cmd: list[str], *, check: bool = True, capture: bool = False) -> subprocess.CompletedProcess[str]:
    """Run a command via sudo (VM storage and libvirt are root-owned)."""
    return run(["sudo", *cmd], check=check, capture=capture)


def virsh(*args: str, check: bool = True) -> subprocess.CompletedProcess[str] | None:
    """Run virsh against the system connection via sudo.

    Returns None if the virsh binary is not installed (so callers can degrade
    gracefully before libvirt packages are installed).
    """
    if not shutil.which("virsh"):
        return None
    return sudo_run(["virsh", "-c", "qemu:///system", *args], check=check, capture=True)


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

    sudo_run(["mkdir", "-p", str(GOLDEN_DIR)])
    cmd = ["sudo", "bash", str(build_script)]
    if force:
        cmd.append("--force")
    result = run(cmd, check=False)
    return result.returncode


def cmd_up(_args: list[str]) -> int:
    """Boot a fresh overlay from the golden base."""
    golden = GOLDEN_DIR / "golden.qcow2"
    if sudo_run(["test", "-f", str(golden)], check=False).returncode != 0:
        print("error: golden base not found. Run 'vm build' first.", file=sys.stderr)
        return 1

    sudo_run(["mkdir", "-p", str(OVERLAY_DIR)])
    overlay = OVERLAY_DIR / f"{VM_NAME}.qcow2"
    if sudo_run(["test", "-f", str(overlay)], check=False).returncode == 0:
        print(f"Overlay already exists: {overlay}")
        print("Destroy first with 'vm destroy', or the VM may already be running.")
        return 1

    # Create overlay from golden (libvirt-qemu needs write access)
    sudo_run([
        "qemu-img", "create", "-f", "qcow2",
        "-b", str(golden), "-F", "qcow2", str(overlay),
    ])
    sudo_run(["chown", "libvirt-qemu:libvirt-qemu", str(overlay)])

    # Read and fill domain template
    if not TEMPLATE.exists():
        print(f"error: domain template not found: {TEMPLATE}", file=sys.stderr)
        return 1

    template_text = TEMPLATE.read_text()
    domain_xml = template_text.format(
        vm_name=VM_NAME,
        memory=VM_MEMORY,
        vcpu=VM_VCPU,
        disk_path=str(overlay),
        virtiofs_dir=VIRTIOFS_DIR,
        mount_tag=MOUNT_TAG,
    )

    # Define and start
    xml_path = OVERLAY_DIR / "domain.xml"
    with tempfile.NamedTemporaryFile("w", suffix=".xml", delete=False) as f:
        f.write(domain_xml)
        tmp_xml = f.name
    sudo_run(["cp", tmp_xml, str(xml_path)])
    Path(tmp_xml).unlink(missing_ok=True)

    virsh("define", str(xml_path))
    virsh("start", VM_NAME)
    print(f"VM '{VM_NAME}' started")

    # Wait for IP and write ssh config
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

    print("Use 'ssh vm' or 'virt-viewer --connect qemu:///system staging-vm'")
    return 0


def cmd_destroy(_args: list[str]) -> int:
    """Tear down the VM and delete the overlay."""
    # Stop if running
    result = virsh("domstate", VM_NAME, check=False)
    if result is None:
        print("  (virsh not installed — run 'make all' to install libvirt)")
    elif result.returncode == 0:
        state = result.stdout.strip()
        if state != "shut off":
            virsh("destroy", VM_NAME, check=False)
        virsh("undefine", VM_NAME, "--remove-all-storage", check=False)
        print(f"VM '{VM_NAME}' destroyed")
    else:
        print(f"VM '{VM_NAME}' is not defined")

    # Remove overlay
    overlay = OVERLAY_DIR / f"{VM_NAME}.qcow2"
    if sudo_run(["test", "-f", str(overlay)], check=False).returncode == 0:
        sudo_run(["rm", "-f", str(overlay)])
        print(f"Overlay removed: {overlay}")

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
        size = int(sudo_run(["stat", "-c", "%s", str(golden)], check=False).stdout.strip())
        print(f"  {golden} ({size / (1024**3):.1f} GiB)")
    else:
        print("  (not built)")

    print("\n=== Overlay ===")
    overlay = OVERLAY_DIR / f"{VM_NAME}.qcow2"
    if sudo_run(["test", "-f", str(overlay)], check=False).returncode == 0:
        size = int(sudo_run(["stat", "-c", "%s", str(overlay)], check=False).stdout.strip())
        print(f"  {overlay} ({size / (1024**2):.1f} MiB)")
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


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

COMMANDS = {
    "build": cmd_build,
    "up": cmd_up,
    "destroy": cmd_destroy,
    "status": cmd_status,
}


def main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[1] not in COMMANDS:
        print("usage: vm <build|up|destroy|status>", file=sys.stderr)
        if len(argv) >= 2 and argv[1] not in COMMANDS:
            print(f"error: unknown command '{argv[1]}'", file=sys.stderr)
            return 1
        return 1

    command = argv[1]
    return COMMANDS[command](argv[2:])


if __name__ == "__main__":
    sys.exit(main(sys.argv))
