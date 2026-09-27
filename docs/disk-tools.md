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

```
lsusb
lsblk -o NAME,SIZE,FSTYPE,LABEL,MOUNTPOINT
blkid; findmnt
udisksctl mount -b /dev/sdX1
udisksctl unmount -b /dev/sdX1
wipefs --no-act /dev/sdX
sudo cfdisk /dev/sdX
sudo mkfs.exfat /dev/sdX1
udisksctl mount -b /dev/sdX1
udisksctl unmount -b /dev/sdX1
udisksctl power-off -b /dev/sdX
```

`udisksctl mount -b` lands at `/run/media/$USER/LABEL`. `power-off -b`
is the safe-removal step (unbinds USB). `eject /dev/sdX` is a legacy
alternative; it does not power-off USB.

## Workflow B — netinstall-native only (no extra packages)

```
dmesg | tail
lsblk -o NAME,SIZE,FSTYPE,LABEL,MOUNTPOINT
sudo mount /dev/sdX1 /mnt
umount /mnt
wipefs --no-act /dev/sdX
sudo cfdisk /dev/sdX
sudo mkfs.ext4 -L DATA /dev/sdX1
sudo mount /dev/sdX1 /mnt
umount /mnt
sync
```

Win+Linux formatting needs one extra package: `exfatprogs` (`mkfs.exfat`).
No `eject` or `power-off` — both are non-native; unplug physically.

## SSD: inspect only

```
lsblk -o NAME,SIZE,FSTYPE,LABEL,MOUNTPOINT
findmnt
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
