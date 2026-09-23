#!/usr/bin/env bash
set -euo pipefail

# ---------------------------------------------------------------------------
# lib/65-vm.sh — Build the golden base image for the staging VM
#
# Module run via install.sh (step "vm-build" sudo bash "$REPO_ROOT/lib/65-vm.sh")
# or standalone: sudo bash lib/65-vm.sh [--force]
# Sources lib/common.sh for logging and utility functions.
#
# Idempotent: exits 2 (SKIP) if the golden base already exists unless --force.
# Also exits 2 (SKIP) if /dev/kvm is unavailable.
#
# Steps:
#   1. Preflight: verify required tools, /dev/kvm, free disk space
#   2. mmdebstrap trixie → rootfs
#   3. Create disk image, partition (ESP + root), format
#   4. Copy rootfs, write fstab, install grub
#   5. Boot once, minimal setup (VM user, sshd, seeded identity)
#   6. Shutdown, freeze as read-only golden qcow2
#
# FUTURE: logging infra is minimal (log_step/log_ok + blind waits like the
# 120s ssh poll). A Python rewrite (mirroring tools/vm.py) would give
# structured logging, per-run guest console capture, and better failure
# diagnostics.
# ---------------------------------------------------------------------------

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
# shellcheck source=../lib/common.sh
source "$REPO_ROOT/lib/common.sh"

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

VM_BASE="/var/lib/libvirt/vm"
GOLDEN_DIR="$VM_BASE/golden"
WORK_DIR=""
VM_USER="vm"
VM_PASS="0000"
VM_HOSTNAME="staging-vm"
VM_DISK_SIZE="${VM_DISK_SIZE:-20G}"
BOOT_TIMEOUT=120
MIN_FREE_GIB="${MIN_FREE_GIB:-22}"
VM_MOUNT_PATH="/home/$VM_USER/$(basename "$REPO_ROOT")"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

cleanup() {
    # Unmount everything under WORK_DIR (deepest first) so rm -rf never
    # descends into a mounted filesystem (e.g. a bind of host /dev).
    if [[ -n "${WORK_DIR:-}" ]]; then
        while read -r _ mnt _ _ _ _; do
            case "$mnt" in
                "$WORK_DIR"/*) printf '%s\n' "$mnt" ;;
            esac
        done < /proc/mounts | sort -r | while read -r mnt; do
            umount "$mnt" 2>/dev/null || true
        done
    fi
    if [[ -n "${ROOT_MOUNT:-}" ]]; then
        rmdir "$ROOT_MOUNT" 2>/dev/null || true
    fi
    if [[ -n "${LOOP_DISK:-}" ]]; then
        losetup -d "$LOOP_DISK" 2>/dev/null || true
    fi
    if [[ -n "${WORK_DIR:-}" && "$WORK_DIR" == "$VM_BASE"/work.* && -d "$WORK_DIR" ]]; then
        if grep -q "^$WORK_DIR/" /proc/mounts; then
            log_warn "Leaving $WORK_DIR in place: still has mounts"
        else
            rm -rf "$WORK_DIR"
        fi
    fi
    if [[ -n "${TEMP_DOMAIN:-}" ]]; then
        virsh destroy "$TEMP_DOMAIN" 2>/dev/null || true
        virsh undefine "$TEMP_DOMAIN" --nvram --remove-all-storage 2>/dev/null || true
    fi
    # Stale partial from an interrupted freeze_golden atomic swap
    rm -f "$GOLDEN_DIR/golden.qcow2.new" 2>/dev/null || true
}
trap cleanup EXIT TERM INT

die() {
    log_error "$*"
    exit 1
}

announce_golden() {
    local state="$1"
    case "$state" in
        built)  log_step "Golden base build complete: $GOLDEN_DIR/golden.qcow2" ;;
        exists) log_step "Golden base already exists: $GOLDEN_DIR/golden.qcow2" ;;
    esac
    log "Use 'vm boot' to boot a fresh overlay"
}

# ---------------------------------------------------------------------------
# Preflight
# ---------------------------------------------------------------------------

preflight() {
    log_step "Checking required tools"
    local missing=()
    for tool in mmdebstrap virsh qemu-img sgdisk mkfs.vfat mkfs.ext4 grub-install ssh-keygen; do
        if ! cmd_exists "$tool"; then
            missing+=("$tool")
        fi
    done
    if (( ${#missing[@]} > 0 )); then
        die "Missing required tools: ${missing[*]}. Run 'make all' first to install packages."
    fi

    if [[ ! -d /sys/firmware/efi ]]; then
        die "Host is not booted in UEFI mode. Cannot install GRUB for UEFI."
    fi

    if [[ "$(id -u)" -ne 0 ]]; then
        die "This script must be run as root (use: sudo bash lib/65-vm.sh)"
    fi

    log_ok "Preflight passed"
}

check_kvm() {
    if [[ ! -e /dev/kvm ]]; then
        log "SKIP: /dev/kvm not available — cannot build the VM"
        exit 2
    fi
}

check_disk_space() {
    log_step "Checking free disk space"
    mkdir -p "$VM_BASE"
    chown libvirt-qemu:libvirt-qemu "$VM_BASE"
    local free_kb min_kb
    free_kb=$(df -Pk "$VM_BASE" | awk 'NR==2 {print $4}')
    min_kb=$((MIN_FREE_GIB * 1024 * 1024))
    if (( free_kb < min_kb )); then
        die "Insufficient free disk space: $((free_kb / 1024 / 1024)) GiB free on $VM_BASE (need >= $MIN_FREE_GIB GiB)"
    fi
    log_ok "Disk space OK ($((free_kb / 1024 / 1024)) GiB free)"
}

ensure_libvirt_group() {
    if [[ -z "${SUDO_USER:-}" ]]; then
        log_warn "SUDO_USER not set — cannot add calling user to libvirt group"
        return 0
    fi
    if id -nG "$SUDO_USER" | tr ' ' '\n' | grep -qx libvirt; then
        log_ok "User '$SUDO_USER' is already in the libvirt group"
        return 0
    fi
    log_step "Adding '$SUDO_USER' to the libvirt group (for virt-viewer)"
    usermod -aG libvirt "$SUDO_USER"
    log_ok "User '$SUDO_USER' added to libvirt group — log out and back in for it to take effect"
}

ensure_libvirtd() {
    if ! systemctl is-active --quiet libvirtd; then
        log "Starting libvirtd"
        systemctl start libvirtd
    fi
    if ! virsh net-list --all --name | grep -qx default; then
        die "Default libvirt network not defined — run 'virsh net-define /etc/libvirt/qemu/networks/default.xml'"
    fi
    if ! virsh net-list --name | grep -qx default; then
        log "Starting default libvirt network"
        virsh net-start default
        virsh net-autostart default
    fi
}

# ---------------------------------------------------------------------------
# Build rootfs with mmdebstrap
# ---------------------------------------------------------------------------

build_rootfs() {
    log_step "Building rootfs with mmdebstrap"
    local rootfs="$WORK_DIR/rootfs"
    mkdir -p "$rootfs"

    mmdebstrap \
        --architectures=amd64 \
        --variant=standard \
        --components=main,non-free-firmware \
        --include=tasksel,task-laptop,network-manager,firmware-iwlwifi,openssh-server,git,sudo,grub-efi-amd64,linux-image-amd64,console-setup,keyboard-configuration,qemu-guest-agent \
        --customize-hook="chroot \"\$1\" useradd -m -G sudo -s /bin/bash '$VM_USER'" \
        --customize-hook="install -d -m 700 \"\$1\"/home/$VM_USER/.ssh" \
        --customize-hook="install -m 600 \"$WORK_DIR/id_ed25519.pub\" \"\$1\"/home/$VM_USER/.ssh/authorized_keys" \
        --customize-hook="chroot \"\$1\" chown -R $VM_USER:$VM_USER /home/$VM_USER/.ssh" \
        --customize-hook="chroot \"\$1\" bash -c \"echo '$VM_USER:$VM_PASS' | chpasswd\"" \
        --customize-hook="chroot \"\$1\" bash -c \"echo '$VM_HOSTNAME' > /etc/hostname\"" \
        --customize-hook="chroot \"\$1\" systemctl enable ssh" \
        --customize-hook="chroot \"\$1\" systemctl enable NetworkManager" \
        --customize-hook="chroot \"\$1\" systemctl enable qemu-guest-agent" \
        trixie "$rootfs"

    # Mirror the host keyboard layout (fr/latin9) so the SPICE console matches
    # the host. console-setup applies this at boot via setupcon.
    cat > "$rootfs/etc/default/keyboard" <<EOF
XKBMODEL="pc105"
XKBLAYOUT="fr"
XKBVARIANT="latin9"
XKBOPTIONS=""
EOF

    log_ok "Rootfs built ($rootfs)"
}

# ---------------------------------------------------------------------------
# Create and partition disk image
# ---------------------------------------------------------------------------

create_disk() {
    log_step "Creating disk image ($VM_DISK_SIZE)"
    local raw="$WORK_DIR/golden.raw"

    # Create raw disk
    qemu-img create -f raw "$raw" "$VM_DISK_SIZE"

    # Partition with GPT: ESP (512M) + root (rest)
    sgdisk --clear \
        --new=1:0:+512M --typecode=1:ef00 \
        --new=2:0:0 --typecode=2:8300 \
        "$raw"

    # Attach the whole disk and let the kernel scan partitions. Partition
    # devices (loopNp1/loopNp2) resolve via sysfs, so grub-probe inside the
    # chroot never reads the loop backing file (the offset-loop approach
    # failed with "failed to get canonical path of .../golden.raw").
    LOOP_DISK=$(losetup --find --show --partscan "$raw")
    for _ in $(seq 1 20); do
        [[ -e "${LOOP_DISK}p1" && -e "${LOOP_DISK}p2" ]] && break
        sleep 0.5
    done
    if [[ ! -e "${LOOP_DISK}p1" || ! -e "${LOOP_DISK}p2" ]]; then
        partx -a "$LOOP_DISK" 2>/dev/null || true
        sleep 1
    fi
    if [[ ! -e "${LOOP_DISK}p1" || ! -e "${LOOP_DISK}p2" ]]; then
        die "Partition scan failed for $LOOP_DISK (no ${LOOP_DISK}p1/p2)"
    fi
    LOOP_EFI="${LOOP_DISK}p1"
    LOOP_ROOT="${LOOP_DISK}p2"
    log "Loop devices: $LOOP_DISK ($LOOP_EFI, $LOOP_ROOT)"

    # Format partitions
    log_step "Formatting partitions"
    mkfs.vfat -F32 -n EFI "$LOOP_EFI"
    mkfs.ext4 -L root "$LOOP_ROOT"

    log_ok "Disk partitioned and formatted"
}

# ---------------------------------------------------------------------------
# Copy rootfs and install bootloader
# ---------------------------------------------------------------------------

populate_disk() {
    log_step "Populating disk"

    ROOT_MOUNT="$WORK_DIR/root"
    mkdir -p "$ROOT_MOUNT"

    # Mount root
    mount "$LOOP_ROOT" "$ROOT_MOUNT"

    # Copy rootfs
    rsync -a "$WORK_DIR/rootfs/" "$ROOT_MOUNT/"

    # Structural mount point for the hostrepo virtiofs share
    mkdir -p "$ROOT_MOUNT$VM_MOUNT_PATH"

    # Write fstab
    local root_uuid
    root_uuid=$(blkid -s UUID -o value "$LOOP_ROOT")
    local esp_uuid
    esp_uuid=$(blkid -s UUID -o value "$LOOP_EFI")

    cat > "$ROOT_MOUNT/etc/fstab" <<EOF
UUID=$root_uuid  /      ext4  errors=remount-ro  0 1
UUID=$esp_uuid   /boot/efi  vfat  umask=0077  0 1
hostrepo  $VM_MOUNT_PATH  virtiofs  ro,nofail  0  0
EOF

    # Mount ESP at /boot/efi and install grub
    mkdir -p "$ROOT_MOUNT/boot/efi"
    mount "$LOOP_EFI" "$ROOT_MOUNT/boot/efi"

    # Bind-mount necessary host paths for grub-install
    mount --bind /dev "$ROOT_MOUNT/dev"
    mount --bind /dev/pts "$ROOT_MOUNT/dev/pts"
    mount -t proc proc "$ROOT_MOUNT/proc"
    mount -t sysfs sysfs "$ROOT_MOUNT/sys"

    # Install grub into chroot
    chroot "$ROOT_MOUNT" grub-install \
        --target=x86_64-efi \
        --efi-directory=/boot/efi \
        --boot-directory=/boot \
        --removable \
        --recheck

    # Serial console on ttyS0 so the libvirt QEMU log captures guest boot output
    sed -i 's/^GRUB_CMDLINE_LINUX=""/GRUB_CMDLINE_LINUX="console=ttyS0"/' \
        "$ROOT_MOUNT/etc/default/grub"

    chroot "$ROOT_MOUNT" update-grub

    # Unmount host bindings
    umount "$ROOT_MOUNT/sys"
    umount "$ROOT_MOUNT/proc"
    umount "$ROOT_MOUNT/dev/pts"
    umount "$ROOT_MOUNT/dev"

    log_ok "Rootfs populated and grub installed"
}

# ---------------------------------------------------------------------------
# Convert raw to qcow2 and freeze as golden
# ---------------------------------------------------------------------------

freeze_golden() {
    log_step "Freezing golden base"

    # Unmount everything
    umount "$ROOT_MOUNT/boot/efi"
    umount "$ROOT_MOUNT"

    # Detach loop device
    losetup -d "$LOOP_DISK"
    unset LOOP_DISK

    # Convert raw to qcow2 (atomic swap: the old golden stays valid until the
    # new image is fully written and renamed over it)
    mkdir -p "$GOLDEN_DIR"
    chown libvirt-qemu:libvirt-qemu "$GOLDEN_DIR"
    local raw="$WORK_DIR/golden.raw"
    qemu-img convert -f raw -O qcow2 "$raw" "$GOLDEN_DIR/golden.qcow2.new"

    # Make golden read-only
    chmod 444 "$GOLDEN_DIR/golden.qcow2.new"

    # Atomic swap: rename over the old golden only once the new image is complete
    mv -f "$GOLDEN_DIR/golden.qcow2.new" "$GOLDEN_DIR/golden.qcow2"

    # Drop the raw intermediate immediately (reduces peak usage during first boot)
    rm -f "$raw"

    log_ok "Golden base frozen at $GOLDEN_DIR/golden.qcow2"
}

# ---------------------------------------------------------------------------
# Render the shared domain template (same tokens as tools/vm.py)
# ---------------------------------------------------------------------------

render_domain_xml() {
    local out="$1" overlay="$2" vm_name="$3"
    python3 - "$REPO_ROOT/vm/domain.xml.template" "$out" "$overlay" "$vm_name" "$REPO_ROOT" <<'PY'
import sys
tpl = open(sys.argv[1]).read()
xml = tpl.format(
    vm_name=sys.argv[4],
    memory="5",
    vcpu="5",
    disk_path=sys.argv[3],
    virtiofs_dir=sys.argv[5],
    mount_tag="hostrepo",
)
open(sys.argv[2], "w").write(xml)
PY
}

# ---------------------------------------------------------------------------
# First boot: minimal setup
# ---------------------------------------------------------------------------

first_boot_setup() {
    log_step "First boot: minimal setup"

    local overlay="$WORK_DIR/overlay.qcow2"
    local golden="$GOLDEN_DIR/golden.qcow2"

    # Create overlay on the read-only golden (overlay backing only needs read)
    qemu-img create -f qcow2 -b "$golden" -F qcow2 "$overlay"

    # libvirt runs QEMU as libvirt-qemu; grant it access to the work dir + overlay
    chmod 755 "$WORK_DIR"
    chown libvirt-qemu:libvirt-qemu "$overlay"

    # Create temporary domain XML from the shared template
    local domain_xml="$WORK_DIR/temp-domain.xml"
    local vm_name="vm-build-temp"
    render_domain_xml "$domain_xml" "$overlay" "$vm_name"

    TEMP_DOMAIN="$vm_name"

    # Define-or-replace: clear any stale domain from a prior failed run
    virsh destroy "$vm_name" 2>/dev/null || true
    virsh undefine "$vm_name" --nvram --remove-all-storage 2>/dev/null || true
    virsh define --validate "$domain_xml"
    virsh start "$vm_name"

    # Wait for ssh to become available
    log "Waiting for VM ssh..."
    local vm_ip=""
    for (( i=0; i<BOOT_TIMEOUT; i+=5 )); do
        # Get VM IP
        vm_ip=$(virsh domifaddr "$vm_name" 2>/dev/null \
            | awk '/ipv4/ && !/127.0.0.1/ {sub(/\/.*/, "", $NF); print $NF}' \
            | head -1)
        if [[ -n "$vm_ip" ]] && ssh -i "$WORK_DIR/id_ed25519" -o BatchMode=yes \
            -o ConnectTimeout=3 -o StrictHostKeyChecking=no \
            -o UserKnownHostsFile=/dev/null \
            "${VM_USER}@${vm_ip}" "echo ready" >/dev/null 2>&1; then
            log_ok "VM is up at $vm_ip"
            break
        fi
        sleep 5
    done

    if [[ -z "$vm_ip" ]]; then
        die "VM did not come up within $BOOT_TIMEOUT seconds"
    fi

    # Smoke test: verify NetworkManager manages the virtio NIC
    log_step "Smoke test: verifying VM network"
    ssh -i "$WORK_DIR/id_ed25519" -o BatchMode=yes \
        -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
        "${VM_USER}@${vm_ip}" "nmcli device status || true"

    # Shutdown the VM
    log "Shutting down VM"
    virsh shutdown "$vm_name"

    # Wait for shutdown
    for (( i=0; i<60; i+=5 )); do
        if [[ "$(virsh domstate "$vm_name" 2>/dev/null)" == "shut off" ]]; then
            break
        fi
        sleep 5
    done

    if [[ "$(virsh domstate "$vm_name" 2>/dev/null)" != "shut off" ]]; then
        die "VM did not shut down within 60 seconds"
    fi

    # Undefine the temporary domain
    virsh undefine "$vm_name" --remove-all-storage 2>/dev/null || true
    unset TEMP_DOMAIN

    # Clean up overlay
    rm -f "$overlay"

    log_ok "First boot setup complete"
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

main() {
    local force=false
    for arg in "$@"; do
        [[ "$arg" == "--force" ]] && force=true
    done

    # Never build a VM inside a VM (install.sh runs this module on the staging
    # VM too; nested virtualization is not the intent).
    if is_vm; then
        log "SKIP: running inside a VM — cannot build the golden base here"
        exit 2
    fi

    # One-time setup: unprivileged virt-viewer access (vm view). Runs before
    # the golden skip so it applies even when the golden already exists.
    ensure_libvirt_group

    # Idempotent skip: complete golden exists and no --force
    if [[ -f "$GOLDEN_DIR/golden.qcow2" && -f "$GOLDEN_DIR/.complete" ]] && [[ "$force" != true ]]; then
        announce_golden exists
        log "Use --force to rebuild."
        exit 2
    fi

    # A rebuild invalidates any prior completion marker
    rm -f "$GOLDEN_DIR/.complete"

    log_step "Starting golden base build"

    check_kvm
    preflight
    check_disk_space
    ensure_libvirtd

    WORK_DIR=$(mktemp -d "$VM_BASE/work.XXXXXX")
    log "Work directory: $WORK_DIR"

    # Ephemeral keypair for non-interactive ssh during first boot; lives in
    # WORK_DIR so it is deleted by the cleanup trap (password auth is the
    # persistent credential for the golden).
    ssh-keygen -t ed25519 -N "" -f "$WORK_DIR/id_ed25519" >/dev/null

    build_rootfs
    create_disk
    populate_disk
    freeze_golden
    first_boot_setup

    # Mark the build complete (idempotency gate: golden without this marker is broken)
    touch "$GOLDEN_DIR/.complete"

    announce_golden built
}

main "$@"