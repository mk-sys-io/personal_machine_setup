# Linux → Windows filesystem access
> Status: Manual procedure · Not currently mounted · Not validated on current machine

Linux reads and writes the Windows NTFS partition directly. No shared partition needed. One-way transfer is a workflow policy: Linux → Windows only, not a filesystem restriction. Re-check the device and UUID with `blkid` before mounting; the values below are machine-specific.

## Prerequisites

- BitLocker: last observed off on all volumes; re-check before mounting.
- Fast Startup: must be disabled (`powercfg /hibernate off` removes hiberfil.sys).
- Partition must not be dirty; re-check before a read-write mount.

## Available NTFS drivers (Debian 13, kernel 6.12)

| Driver | Type | Since | Performance | Userspace tools |
|--------|------|-------|-------------|-----------------|
| `ntfs3` | Kernel-native | 5.15 | Better | None (use ntfs-3g tools) |
| `ntfs-3g` | FUSE | — | Slower | `ntfsfix`, `ntfsresize`, etc. (installed) |

Both support read/write. No objectively better one. `ntfs3` is faster; `ntfs-3g` has better tooling.

## Auto-mount (fstab)

Add to `/etc/fstab`:

```
UUID=848A7D498A7D38AE  /mnt/windows  ntfs3  defaults,windows_names,uid=1000,gid=1000,umask=022  0  0
```

- `windows_names`: prevents creating files with names Windows can't handle (e.g., `:`, `*`, `?`).
- `uid/gid/umask`: maps ownership to the user (1000).
- Mounts automatically at boot.

Alternative: `udisks` (GUI file managers auto-mount via udisks, prefers ntfs3 by default). No config needed.

## Dirty partition handling

A "dirty" NTFS partition has a flag set indicating it was not cleanly unmounted. Causes: power loss, Linux crash/panic during write, interrupted write.

- `ntfs3` refuses to mount dirty partitions without `force` (mounts read-only or fails).
- `ntfs-3g` mounts read-only or warns.
- Fix: `sudo ntfsfix --clear-dirty /dev/nvme0n1p4` (from ntfs-3g tools) or clean Windows boot.
- Last resort: `sudo mount -t ntfs3 -o force /dev/nvme0n1p4 /mnt` (risky, may cause data loss).

In a dual-boot setup, Windows cannot crash during a Linux write (Windows is not running). The only dirty-flag risks are Linux-side: power loss or kernel crash during the write.

## Windows partition details

- UUID: `848A7D498A7D38AE`
- Device: `/dev/nvme0n1p4`
- Size: 185.48 GB
- Filesystem: NTFS
- Cluster size: 4 KB
- Not dirty (clean)

## No shared partition needed

Linux writes directly to the Windows NTFS partition. The future kill-switch flag file and any transferred files would go directly to `/mnt/windows/`. No additional partition creation is required.