# Disk tools quick-start

Scope: USB sticks (full workflow) + internal NVMe SSD (inspect + usage
only, never partition or format live). Mobile SD cards (`mmcblk*`) out.

## Prerequisites

| Tool | Package (`packages/apt.txt`) | Netinstall-native? |
|---|---|---|
| `lsblk`, `blkid`, `findmnt`, `wipefs`, `dmesg` | `util-linux` | yes |
| `cfdisk`, `fdisk`, `sfdisk` | `fdisk` | yes |
| `mount`, `umount` | `mount` | yes |
| `mkfs.ext4` | `e2fsprogs` | yes |
| `eject` | `eject` (`:73`) | no — must install |
| `mkfs.exfat` | `exfatprogs` (`:74`) | no — must install |
| `mkfs.vfat` | `dosfstools` (`:204`) | no — must install |
| `udisksctl` | `udisks2` (`:205`) | no — must install |
| `lsusb` | `usbutils` (`:206`) | no — must install |
| `ncdu` | `ncdu` (`:207`) | no — must install |

Win+Linux default FS is exFAT (`mkfs.exfat`). FAT32 (`mkfs.vfat -F 32`)
is small/legacy only. `mkfs.ext4 -L DATA` is Linux-only.

## Workflow A — with utilities (Sway, rootless mount)

Find the plugged stick:

```bash
lsusb
```

Map devices — identify `/dev/sdX`:

```bash
lsblk -o NAME,SIZE,FSTYPE,LABEL,MOUNTPOINT
```

Confirm filesystem and mount state before any write:

```bash
blkid; findmnt
```

Mount rootlessly (lands at `/run/media/$USER/LABEL`):

```bash
udisksctl mount -b /dev/sdX1
```

Unmount before formatting — never `mkfs` a mounted FS:

```bash
udisksctl unmount -b /dev/sdX1
```

Preview existing signatures (read-only):

```bash
wipefs --no-act /dev/sdX
```

Repartition if needed (USB only, never live SSD):

```bash
sudo cfdisk /dev/sdX
```

Format the partition Win+Linux exFAT:

```bash
sudo mkfs.exfat /dev/sdX1
```

Remount to verify:

```bash
udisksctl mount -b /dev/sdX1
```

Unmount for removal:

```bash
udisksctl unmount -b /dev/sdX1
```

Power off the stick (safe removal; `eject /dev/sdX` is a legacy
alternative, not USB power-off):

```bash
udisksctl power-off -b /dev/sdX
```

## Workflow B — netinstall-native only (no extra packages)

Check kernel messages for the just-inserted stick:

```bash
dmesg | tail
```

Map devices — identify `/dev/sdX`:

```bash
lsblk -o NAME,SIZE,FSTYPE,LABEL,MOUNTPOINT
```

Mount to inspect:

```bash
sudo mount /dev/sdX1 /mnt
```

Unmount before formatting — never `mkfs` a mounted FS:

```bash
umount /mnt
```

Preview existing signatures (read-only):

```bash
wipefs --no-act /dev/sdX
```

Repartition if needed (USB only, never live SSD):

```bash
sudo cfdisk /dev/sdX
```

Format the partition Linux-only ext4 (Win+Linux needs one extra
package: `exfatprogs`):

```bash
sudo mkfs.ext4 -L DATA /dev/sdX1
```

Remount to verify:

```bash
sudo mount /dev/sdX1 /mnt
```

Unmount for removal:

```bash
umount /mnt
```

Flush writes:

```bash
sync
```

Unplug physically (`eject`/`power-off` are both non-native):

```bash
echo "safe to unplug /dev/sdX"
```

## SSD: inspect only

Map devices:

```bash
lsblk -o NAME,SIZE,FSTYPE,LABEL,MOUNTPOINT
```

Show mounted filesystems:

```bash
findmnt
```

Drill into directory-level usage:

```bash
ncdu /
```

Never partition or format the live internal SSD from the running system.

## Safe delete

`trash-put FILE; trash-list` is the safe-delete alternative to `rm`.
Trash semantics (FreeDesktop client-side convention, no daemon needed)
are owned by `30 §30.3`; not explained here.

## Safety

- Triple-check `/dev/sdX` before any write — wrong device destroys data.
- Never `mkfs` a mounted FS — `findmnt` to confirm unmounted first.
- `wipefs --no-act` is read-only preview; drop `--no-act` only when you
  are ready to destroy.
