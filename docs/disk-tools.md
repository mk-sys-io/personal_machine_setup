# Disk tools quick-start

Scope: internal SSD (inspect + usage only) and manually-plugged USB sticks
(full workflow). No SD cards. Every tool has `--help` / man page — see there for details.

## Quick reference

| Tool | Use for | Example |
|---|---|---|
| `lsusb` | find plugged USB stick | `lsusb` |
| `lsblk -o NAME,SIZE,FSTYPE,LABEL,MOUNTPOINT` | map device, SSD or USB | same |
| `blkid`, `findmnt` | confirm FS before any write | `blkid; findmnt` |
| `ncdu` | dir-level usage, SSD or USB | `ncdu /` or `ncdu /run/media/$USER/STICK` |
| `trash-put`, `trash-list`, `trash-empty` | safe delete | `trash-put FILE; trash-list` |
| `cfdisk`, `fdisk` | partition USB only, never live SSD | `sudo cfdisk /dev/sdX` |
| `mkfs.exfat` | USB default Win+Linux | `sudo mkfs.exfat /dev/sdX1` |
| `mkfs.vfat -F 32` | USB small/legacy only | `sudo mkfs.vfat -F 32 /dev/sdX1` |
| `mkfs.ext4` | USB Linux-only | `sudo mkfs.ext4 -L DATA /dev/sdX1` |
| `udisksctl` | rootless USB mount on Sway | `udisksctl mount -b /dev/sdX1` |

## SSD: inspect only

```
lsblk -o NAME,SIZE,FSTYPE,LABEL,MOUNTPOINT
findmnt
ncdu /
```

Never partition or format the live internal SSD from the running system.

## USB stick: dual-use workflow

```
lsusb
lsblk -o NAME,SIZE,FSTYPE,LABEL,MOUNTPOINT
wipefs --no-act /dev/sdX
sudo cfdisk /dev/sdX
sudo mkfs.exfat /dev/sdX1
udisksctl mount -b /dev/sdX1
```

## Safety

Triple-check `/dev/sdX` before any write — wrong device destroys data.
