#!/usr/bin/env python3
"""Build the golden base image for the staging VM.

Converted from lib/65-vm.sh (logic-only port): stdlib only, structured
logging, bounded deadline polls, per-run guest console capture.

Module run manually as root: sudo python3 lib/65-vm.py [--force]
Never wired into the install.py step table (it exit-2s inside any VM by
design, which is exactly where the final gate runs).

Idempotent: exits 2 (SKIP) if the golden base already exists unless
--force. Also exits 2 (SKIP) if /dev/kvm is unavailable or inside a VM.

Steps:
  1. Preflight: verify required tools, /dev/kvm, free disk space
  2. mmdebstrap trixie → rootfs
  3. Create disk image, partition (ESP + root), format
  4. Copy rootfs, write fstab, install grub
  5. Boot once, minimal setup (VM user, sshd, seeded identity)
  6. Shutdown, freeze as read-only golden qcow2

Exit codes:
    0  pass (golden built)
    1  failure
    2  skipped (in VM, no /dev/kvm, golden already exists)
"""
from __future__ import annotations

import atexit
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

VM_BASE = Path("/var/lib/libvirt/vm")
GOLDEN_DIR = VM_BASE / "golden"
VM_USER = "vm"
VM_PASS = "0000"
VM_HOSTNAME = "staging-vm"
VM_DISK_SIZE = os.environ.get("VM_DISK_SIZE", "20G")
BOOT_TIMEOUT = 120
MIN_FREE_GIB = int(os.environ.get("MIN_FREE_GIB", "22"))
VM_MOUNT_PATH = f"/home/{VM_USER}/{REPO_ROOT.name}"

REQUIRED_TOOLS = ["mmdebstrap", "virsh", "qemu-img", "sgdisk", "mkfs.vfat",
                  "mkfs.ext4", "grub-install", "ssh-keygen"]

MMDEBSTRAP_INCLUDE = ("tasksel,task-laptop,network-manager,firmware-iwlwifi,"
                      "openssh-server,git,sudo,grub-efi-amd64,linux-image-amd64,"
                      "console-setup,keyboard-configuration,qemu-guest-agent")


@dataclass
class BuildState:
    work_dir: Path | None = None
    loop_disk: str | None = None
    root_mount: Path | None = None
    temp_domain: str | None = None


STATE = BuildState()


def log(msg: str) -> None:
    print(f"vm-build: {msg}")


def log_ok(msg: str) -> None:
    print(f"vm-build: OK: {msg}")


def log_warn(msg: str) -> None:
    print(f"vm-build: WARNING: {msg}")


def run(cmd: list[str], what: str, **kwargs) -> subprocess.CompletedProcess:
    log(f"{what}: {' '.join(cmd)}")
    r = subprocess.run(cmd, **kwargs)
    if r.returncode != 0:
        raise RuntimeError(f"{what} failed (rc={r.returncode})")
    return r


def is_vm() -> bool:
    # Mirrors common.sh is_vm (systemd-detect-virt: rc 0 = virtualized).
    return subprocess.run(["systemd-detect-virt", "-q"],
                          capture_output=True).returncode == 0


def _mounted_under(path: Path) -> list[str]:
    try:
        mounts = Path("/proc/mounts").read_text().splitlines()
    except OSError:
        return []
    found = [ln.split()[1] for ln in mounts
             if len(ln.split()) > 1 and ln.split()[1].startswith(str(path) + "/")]
    return sorted(found, reverse=True)


def cleanup() -> None:
    # Unmount everything under WORK_DIR (deepest first) so cleanup never
    # descends into a mounted filesystem (e.g. a bind of host /dev).
    if STATE.work_dir is not None:
        for mnt in _mounted_under(STATE.work_dir):
            subprocess.run(["umount", mnt], capture_output=True)
    if STATE.root_mount is not None:
        try:
            STATE.root_mount.rmdir()
        except OSError:
            pass
    if STATE.loop_disk is not None:
        subprocess.run(["losetup", "-d", STATE.loop_disk], capture_output=True)
    wd = STATE.work_dir
    if wd is not None and str(wd).startswith(str(VM_BASE) + "/work.") and wd.is_dir():
        if any(ln.split()[0] == str(wd) or ln.split()[1].startswith(str(wd) + "/")
               for ln in Path("/proc/mounts").read_text().splitlines()
               if len(ln.split()) > 1):
            log_warn(f"leaving {wd} in place: still has mounts")
        else:
            shutil.rmtree(wd, ignore_errors=True)
    if STATE.temp_domain is not None:
        subprocess.run(["virsh", "destroy", STATE.temp_domain], capture_output=True)
        subprocess.run(["virsh", "undefine", STATE.temp_domain, "--nvram",
                        "--remove-all-storage"], capture_output=True)
    # Stale partial from an interrupted freeze_golden atomic swap.
    try:
        (GOLDEN_DIR / "golden.qcow2.new").unlink()
    except OSError:
        pass


atexit.register(cleanup)


def preflight() -> None:
    log("checking required tools")
    missing = [t for t in REQUIRED_TOOLS if shutil.which(t) is None]
    if missing:
        raise RuntimeError(
            f"missing required tools: {' '.join(missing)}. "
            "Run 'make all' first to install packages.")
    if not Path("/sys/firmware/efi").is_dir():
        raise RuntimeError("host is not booted in UEFI mode. "
                           "Cannot install GRUB for UEFI.")
    if os.geteuid() != 0:
        raise RuntimeError("must be run as root (use: sudo python3 lib/65-vm.py)")
    log_ok("preflight passed")


def check_kvm() -> bool:
    if not Path("/dev/kvm").exists():
        log("SKIP: /dev/kvm not available — cannot build the VM")
        return False
    return True


def check_disk_space() -> None:
    log("checking free disk space")
    VM_BASE.mkdir(parents=True, exist_ok=True)
    shutil.chown(VM_BASE, "libvirt-qemu", "libvirt-qemu")
    usage = shutil.disk_usage(VM_BASE)
    free_gib = usage.free // (1024 ** 3)
    if usage.free < MIN_FREE_GIB * 1024 ** 3:
        raise RuntimeError(
            f"insufficient free disk space: {free_gib} GiB free on {VM_BASE} "
            f"(need >= {MIN_FREE_GIB} GiB)")
    log_ok(f"disk space OK ({free_gib} GiB free)")


def ensure_libvirt_group() -> None:
    sudo_user = os.environ.get("SUDO_USER")
    if not sudo_user:
        log_warn("SUDO_USER not set — cannot add calling user to libvirt group")
        return
    r = subprocess.run(["id", "-nG", sudo_user], capture_output=True, text=True)
    if "libvirt" in r.stdout.split():
        log_ok(f"user '{sudo_user}' is already in the libvirt group")
        return
    log(f"adding '{sudo_user}' to the libvirt group (for virt-viewer)")
    run(["usermod", "-aG", "libvirt", sudo_user], "usermod")
    log_ok(f"user '{sudo_user}' added to libvirt group — "
           "log out and back in for it to take effect")


def deploy_polkit_rule() -> None:
    """VM-adjacent privilege policy: libvirt group access for virt-viewer.

    Host-only (below the is_vm gate); called before the golden skip so
    re-runs still converge it. Moved from 40-system_config.sh (dissolve).
    """
    log("polkit rules (libvirt)")
    rules_dir = REPO_ROOT / "system" / "polkit-1" / "rules.d"
    if not rules_dir.is_dir():
        log_warn("polkit rules: source dir not found — skipping")
        return
    live = Path("/etc/polkit-1/rules.d")
    live.mkdir(parents=True, exist_ok=True)
    for src in sorted(rules_dir.glob("*.rules")):
        dst = live / src.name
        if dst.is_file() and dst.read_bytes() == src.read_bytes():
            log_ok(f"polkit rule: {src.name} already up to date")
        else:
            shutil.copy2(src, dst)
            os.chmod(dst, 0o644)
            log_ok(f"polkit rule: {src.name} deployed")
    r = subprocess.run(["systemctl", "reload", "polkit"], capture_output=True)
    if r.returncode != 0:
        log_warn("polkit: reload failed")


def ensure_libvirtd() -> None:
    if subprocess.run(["systemctl", "is-active", "--quiet", "libvirtd"]).returncode != 0:
        log("starting libvirtd")
        run(["systemctl", "start", "libvirtd"], "start libvirtd")
    r = subprocess.run(["virsh", "net-list", "--all", "--name"],
                       capture_output=True, text=True)
    if "default" not in r.stdout.split():
        raise RuntimeError("default libvirt network not defined — run "
                           "'virsh net-define "
                           "/etc/libvirt/qemu/networks/default.xml'")
    r = subprocess.run(["virsh", "net-list", "--name"],
                       capture_output=True, text=True)
    if "default" not in r.stdout.split():
        log("starting default libvirt network")
        run(["virsh", "net-start", "default"], "net-start default")
        run(["virsh", "net-autostart", "default"], "net-autostart default")


def build_rootfs(work_dir: Path) -> None:
    log("building rootfs with mmdebstrap")
    rootfs = work_dir / "rootfs"
    rootfs.mkdir(parents=True, exist_ok=True)
    key_pub = work_dir / "id_ed25519.pub"
    hooks = [
        f"chroot \"$1\" useradd -m -G sudo -s /bin/bash '{VM_USER}'",
        f"install -d -m 700 \"$1\"/home/{VM_USER}/.ssh",
        f"install -m 600 \"{key_pub}\" \"$1\"/home/{VM_USER}/.ssh/authorized_keys",
        f"chroot \"$1\" chown -R {VM_USER}:{VM_USER} /home/{VM_USER}/.ssh",
        f"chroot \"$1\" bash -c \"echo '{VM_USER}:{VM_PASS}' | chpasswd\"",
        f"chroot \"$1\" bash -c \"echo '{VM_HOSTNAME}' > /etc/hostname\"",
        "chroot \"$1\" systemctl enable ssh",
        "chroot \"$1\" systemctl enable NetworkManager",
        "chroot \"$1\" systemctl enable qemu-guest-agent",
    ]
    cmd = ["mmdebstrap", "--architectures=amd64", "--variant=standard",
           "--components=main,non-free-firmware",
           f"--include={MMDEBSTRAP_INCLUDE}"]
    cmd += [f"--customize-hook={h}" for h in hooks]
    cmd += ["trixie", str(rootfs)]
    run(cmd, "mmdebstrap")
    # Mirror the host keyboard layout (fr/latin9) so the SPICE console
    # matches the host. console-setup applies this at boot via setupcon.
    (rootfs / "etc" / "default" / "keyboard").write_text(
        'XKBMODEL="pc105"\nXKBLAYOUT="fr"\nXKBVARIANT="latin9"\nXKBOPTIONS=""\n')
    log_ok(f"rootfs built ({rootfs})")


def wait_for_paths(paths: list[Path], timeout: float, what: str) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if all(p.exists() for p in paths):
            return
        time.sleep(0.5)
    missing = [str(p) for p in paths if not p.exists()]
    raise RuntimeError(f"{what}: {', '.join(missing)}")


def create_disk(work_dir: Path) -> tuple[str, str]:
    log(f"creating disk image ({VM_DISK_SIZE})")
    raw = work_dir / "golden.raw"
    run(["qemu-img", "create", "-f", "raw", str(raw), VM_DISK_SIZE],
        "qemu-img create")
    run(["sgdisk", "--clear", "--new=1:0:+512M", "--typecode=1:ef00",
         "--new=2:0:0", "--typecode=2:8300", str(raw)], "sgdisk")
    # Attach the whole disk and let the kernel scan partitions. Partition
    # devices (loopNp1/loopNp2) resolve via sysfs, so grub-probe inside the
    # chroot never reads the loop backing file (the offset-loop approach
    # failed with "failed to get canonical path of .../golden.raw").
    r = run(["losetup", "--find", "--show", "--partscan", str(raw)],
            "losetup", capture_output=True, text=True)
    loop = r.stdout.strip()
    STATE.loop_disk = loop
    efi, root = Path(loop + "p1"), Path(loop + "p2")
    try:
        wait_for_paths([efi, root], 10.0, "partition scan")
    except RuntimeError:
        subprocess.run(["partx", "-a", loop], capture_output=True)
        time.sleep(1)
        wait_for_paths([efi, root], 10.0, f"partition scan failed for {loop}")
    log(f"loop devices: {loop} ({efi}, {root})")
    log("formatting partitions")
    run(["mkfs.vfat", "-F32", "-n", "EFI", str(efi)], "mkfs.vfat")
    run(["mkfs.ext4", "-L", "root", str(root)], "mkfs.ext4")
    log_ok("disk partitioned and formatted")
    return str(efi), str(root)


def blkid_uuid(dev: str) -> str:
    r = run(["blkid", "-s", "UUID", "-o", "value", dev],
            "blkid", capture_output=True, text=True)
    return r.stdout.strip()


def populate_disk(work_dir: Path, loop_efi: str, loop_root: str) -> None:
    log("populating disk")
    root_mount = work_dir / "root"
    root_mount.mkdir(parents=True, exist_ok=True)
    STATE.root_mount = root_mount
    run(["mount", loop_root, str(root_mount)], "mount root")
    try:
        run(["rsync", "-a", f"{work_dir}/rootfs/", f"{root_mount}/"], "rsync")
        (root_mount / VM_MOUNT_PATH.lstrip("/")).mkdir(parents=True, exist_ok=True)
        root_uuid = blkid_uuid(loop_root)
        esp_uuid = blkid_uuid(loop_efi)
        (root_mount / "etc" / "fstab").write_text(
            f"UUID={root_uuid}  /      ext4  errors=remount-ro  0 1\n"
            f"UUID={esp_uuid}   /boot/efi  vfat  umask=0077  0 1\n"
            f"hostrepo  {VM_MOUNT_PATH}  virtiofs  ro,nofail  0  0\n")
        esp = root_mount / "boot" / "efi"
        esp.mkdir(parents=True, exist_ok=True)
        run(["mount", loop_efi, str(esp)], "mount ESP")
        try:
            for src, tgt in [("/dev", "dev"), ("/dev/pts", "dev/pts")]:
                run(["mount", "--bind", src, str(root_mount / tgt)],
                    f"bind {src}")
            for fstype, tgt in [("proc", "proc"), ("sysfs", "sys")]:
                run(["mount", "-t", fstype, fstype, str(root_mount / tgt)],
                    f"mount {tgt}")
            run(["chroot", str(root_mount), "grub-install",
                 "--target=x86_64-efi", "--efi-directory=/boot/efi",
                 "--boot-directory=/boot", "--removable", "--recheck"],
                "grub-install")
            # Serial console on ttyS0 so the libvirt QEMU log captures
            # guest boot output.
            grub = root_mount / "etc" / "default" / "grub"
            grub.write_text(re.sub(r'^GRUB_CMDLINE_LINUX=""',
                                   'GRUB_CMDLINE_LINUX="console=ttyS0"',
                                   grub.read_text(), flags=re.MULTILINE))
            run(["chroot", str(root_mount), "update-grub"], "update-grub")
        finally:
            for tgt in ["sys", "proc", "dev/pts", "dev"]:
                subprocess.run(["umount", str(root_mount / tgt)],
                               capture_output=True)
        log_ok("rootfs populated and grub installed")
    except Exception:
        subprocess.run(["umount", str(root_mount / "boot" / "efi")],
                       capture_output=True)
        subprocess.run(["umount", str(root_mount)], capture_output=True)
        STATE.root_mount = None
        raise


def freeze_golden(work_dir: Path) -> None:
    log("freezing golden base")
    root_mount = work_dir / "root"
    subprocess.run(["umount", str(root_mount / "boot" / "efi")], capture_output=True)
    subprocess.run(["umount", str(root_mount)], capture_output=True)
    STATE.root_mount = None
    if STATE.loop_disk:
        subprocess.run(["losetup", "-d", STATE.loop_disk], capture_output=True)
        STATE.loop_disk = None
    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    shutil.chown(GOLDEN_DIR, "libvirt-qemu", "libvirt-qemu")
    raw = work_dir / "golden.raw"
    new = GOLDEN_DIR / "golden.qcow2.new"
    # Atomic swap: the old golden stays valid until the new image is fully
    # written and renamed over it.
    run(["qemu-img", "convert", "-f", "raw", "-O", "qcow2", str(raw), str(new)],
        "qemu-img convert")
    os.chmod(new, 0o444)
    os.replace(new, GOLDEN_DIR / "golden.qcow2")
    # Drop the raw intermediate immediately (reduces peak usage).
    try:
        raw.unlink()
    except OSError:
        pass
    log_ok(f"golden base frozen at {GOLDEN_DIR / 'golden.qcow2'}")


def render_domain_xml(out: Path, overlay: Path, vm_name: str) -> None:
    # Same tokens as tools/vm.py against the shared vm/domain.xml.template.
    tpl = (REPO_ROOT / "vm" / "domain.xml.template").read_text()
    out.write_text(tpl.format(vm_name=vm_name, memory="5", vcpu="5",
                              disk_path=str(overlay),
                              virtiofs_dir=str(REPO_ROOT),
                              mount_tag="hostrepo"))


def ssh_up(vm_ip: str, key: Path, timeout: int = 3) -> bool:
    r = subprocess.run(
        ["ssh", "-i", str(key), "-o", "BatchMode=yes",
         "-o", f"ConnectTimeout={timeout}", "-o", "StrictHostKeyChecking=no",
         "-o", "UserKnownHostsFile=/dev/null",
         f"{VM_USER}@{vm_ip}", "echo ready"],
        capture_output=True)
    return r.returncode == 0


def guest_ip(vm_name: str) -> str:
    r = subprocess.run(["virsh", "domifaddr", vm_name],
                       capture_output=True, text=True)
    for line in r.stdout.splitlines():
        if "ipv4" in line and "127.0.0.1" not in line:
            return line.split()[-1].split("/")[0]
    return ""


def wait_for_ssh(vm_name: str, key: Path) -> str:
    log("waiting for VM ssh...")
    deadline = time.monotonic() + BOOT_TIMEOUT
    vm_ip = ""
    while time.monotonic() < deadline:
        vm_ip = guest_ip(vm_name)
        if vm_ip and ssh_up(vm_ip, key):
            log_ok(f"VM is up at {vm_ip}")
            return vm_ip
        time.sleep(5)
    raise RuntimeError(f"VM did not come up within {BOOT_TIMEOUT} seconds")


def wait_for_state(vm_name: str, want: str, timeout: float, what: str) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        r = subprocess.run(["virsh", "domstate", vm_name],
                           capture_output=True, text=True)
        if r.stdout.strip() == want:
            return
        time.sleep(5)
    raise RuntimeError(what)


def capture_guest_console(vm_name: str, tail: int = 200) -> None:
    """Per-run guest console capture: the serial console (ttyS0) lands in
    the libvirt QEMU log — replay its tail into the transcript."""
    qemu_log = Path(f"/var/log/libvirt/qemu/{vm_name}.log")
    print(f"vm-build: --- guest console tail ({qemu_log}) ---")
    try:
        lines = qemu_log.read_text(errors="replace").splitlines()
    except OSError as e:
        print(f"vm-build: console log unavailable: {e}")
        return
    for line in lines[-tail:]:
        print(f"vm-build: [guest] {line}")
    print("vm-build: --- end guest console ---")


def first_boot_setup(work_dir: Path) -> None:
    log("first boot: minimal setup")
    overlay = work_dir / "overlay.qcow2"
    golden = GOLDEN_DIR / "golden.qcow2"
    # Overlay on the read-only golden (backing only needs read).
    run(["qemu-img", "create", "-f", "qcow2", "-b", str(golden),
         "-F", "qcow2", str(overlay)], "qemu-img overlay")
    # libvirt runs QEMU as libvirt-qemu; grant access to work dir + overlay.
    os.chmod(work_dir, 0o755)
    shutil.chown(overlay, "libvirt-qemu", "libvirt-qemu")
    domain_xml = work_dir / "temp-domain.xml"
    vm_name = "vm-build-temp"
    render_domain_xml(domain_xml, overlay, vm_name)
    STATE.temp_domain = vm_name
    try:
        # Define-or-replace: clear any stale domain from a prior failed run.
        subprocess.run(["virsh", "destroy", vm_name], capture_output=True)
        subprocess.run(["virsh", "undefine", vm_name, "--nvram",
                        "--remove-all-storage"], capture_output=True)
        run(["virsh", "define", "--validate", str(domain_xml)], "virsh define")
        run(["virsh", "start", vm_name], "virsh start")
        key = work_dir / "id_ed25519"
        vm_ip = wait_for_ssh(vm_name, key)
        # Smoke test: verify NetworkManager manages the virtio NIC
        # (informational — eyeball evidence only, never gates the build).
        log("smoke test: verifying VM network")
        subprocess.run(
            ["ssh", "-i", str(key), "-o", "BatchMode=yes",
             "-o", "StrictHostKeyChecking=no",
             "-o", "UserKnownHostsFile=/dev/null",
             f"{VM_USER}@{vm_ip}", "nmcli device status || true"])
        log("shutting down VM")
        run(["virsh", "shutdown", vm_name], "virsh shutdown")
        wait_for_state(vm_name, "shut off", 60.0,
                       "VM did not shut down within 60 seconds")
        capture_guest_console(vm_name)
    finally:
        subprocess.run(["virsh", "undefine", vm_name, "--remove-all-storage"],
                       capture_output=True)
        STATE.temp_domain = None
    try:
        overlay.unlink()
    except OSError:
        pass
    log_ok("first boot setup complete")


def announce_golden(state: str) -> None:
    if state == "built":
        log(f"golden base build complete: {GOLDEN_DIR / 'golden.qcow2'}")
    else:
        log(f"golden base already exists: {GOLDEN_DIR / 'golden.qcow2'}")
    log("use 'vm boot' to boot a fresh overlay")


def main(argv: list[str]) -> int:
    if {"-h", "--help"} & set(argv[1:]):
        print("Build the golden base image for the staging VM.")
        print("usage: sudo python3 lib/65-vm.py [--force]")
        return 0
    force = "--force" in argv[1:]

    # Never build a VM inside a VM (nested virtualization is not the intent).
    if is_vm():
        log("SKIP: running inside a VM — cannot build the golden base here")
        return 2

    # One-time setup: unprivileged virt-viewer access + libvirt polkit rule.
    # Runs before the golden skip so it applies even when golden exists.
    ensure_libvirt_group()
    try:
        deploy_polkit_rule()
    except (OSError, RuntimeError) as e:
        print(f"vm-build: ERROR {e}", file=sys.stderr)
        return 1

    # Idempotent skip: complete golden exists and no --force.
    if ((GOLDEN_DIR / "golden.qcow2").is_file()
            and (GOLDEN_DIR / ".complete").is_file() and not force):
        announce_golden("exists")
        log("use --force to rebuild.")
        return 2

    # A rebuild invalidates any prior completion marker.
    try:
        (GOLDEN_DIR / ".complete").unlink()
    except OSError:
        pass

    log("starting golden base build")
    try:
        if not check_kvm():
            return 2
        preflight()
        check_disk_space()
        ensure_libvirtd()

        work_dir = Path(tempfile.mkdtemp(prefix="work.", dir=str(VM_BASE)))
        STATE.work_dir = work_dir
        log(f"work directory: {work_dir}")

        # Ephemeral keypair for non-interactive ssh during first boot; lives
        # in WORK_DIR so cleanup deletes it (password auth is the persistent
        # credential for the golden).
        run(["ssh-keygen", "-t", "ed25519", "-N", "",
             "-f", str(work_dir / "id_ed25519")], "ssh-keygen",
            capture_output=True)

        build_rootfs(work_dir)
        loop_efi, loop_root = create_disk(work_dir)
        populate_disk(work_dir, loop_efi, loop_root)
        freeze_golden(work_dir)
        first_boot_setup(work_dir)

        # Idempotency gate: golden without this marker is broken.
        (GOLDEN_DIR / ".complete").touch()
        announce_golden("built")
        print('RESULT {"status": "OK", "changed": true, '
              '"message": "golden base built"}')
        return 0
    except (OSError, RuntimeError) as e:
        print(f"vm-build: ERROR {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
