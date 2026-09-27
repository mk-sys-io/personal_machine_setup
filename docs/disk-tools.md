# Disk tools quick-start

Scope: USB sticks (full workflow) + internal NVMe SSD (inspect + usage
only, never partition or format live). Mobile SD cards (`mmcblk*`) out.

## Prerequisites

| Tool | What it does | Netinstall-native? |
|---|---|---|
| `lsblk` | list disks, partitions, sizes, filesystems, mountpoints | yes |
| `blkid` | show filesystem type, label, UUID of a device | yes |
| `findmnt` | show what is mounted where | yes |
| `wipefs` | preview (`--no-act`) or erase filesystem signatures | yes |
| `dmesg` | kernel messages — spot the just-inserted stick | yes |
| `cfdisk` | TUI partition editor (create, resize, delete) | yes |
| `fdisk`, `sfdisk` | CLI / scriptable partition editors | yes |
| `mount`, `umount` | attach / detach filesystems | yes |
| `mkfs.ext4` | format a partition Linux-only ext4 | yes |
| `eject` | unmount + SCSI stop (LED off) for removable media | no — must install |
| `mkfs.exfat` | format a partition Win+Linux exFAT (dual-OS default) | no — must install |
| `mkfs.vfat` | format a partition FAT32 (small/legacy media only) | no — must install |
| `udisksctl` | rootless mount / unmount / power-off on Sway | no — must install |
| `lsusb` | list plugged USB devices | no — must install |
| `ncdu` | TUI drill-down into what is eating space by folder | no — must install |

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

Safe to unplug — `umount`+`sync` flushed all writes. (`eject` would only
add a SCSI stop/LED-off; needs the `eject` package, `apt.txt:73`.
`udisksctl power-off` additionally unbinds the device; needs `udisks2`.)

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
