# Linux → Windows NTFS access
> Status: Manual procedure · Not currently mounted · Not validated on current machine

## Purpose

Document the manual procedure for mounting the Windows NTFS partition read/write from Debian to transfer games and other files onto the Windows C: volume.

## Current machine mapping

The target Windows C: partition is `/dev/nvme0n1p4` (185.5 GB NTFS, UUID `848A7D498A7D38AE`); `/dev/nvme0n1p5` is the 642 MB WinRE partition; `/dev/nvme0n1p3` is the 16 MB Microsoft reserved partition. Neither NTFS partition is currently mounted on Linux. `/etc/fstab` has no Windows entry and `/mnt/windows` is not mounted. UUID and device names are machine-specific and must be re-checked with `blkid` before use.

## One-way is a policy, not a filesystem mode

"One-way" describes workflow policy, not a filesystem mode. The mount is read/write; the user controls direction by only writing from Linux to Windows and never modifying Windows files from Linux except as part of the intended transfer. This is friction, not a security boundary.

## Mounting

Manual, read/write, using `ntfs3` or `ntfs-3g`. Confirm the device and UUID with `blkid` first. Create a mount point (e.g. `/mnt/windows`) and mount the partition. Unmount cleanly when done.

## Dirty-partition handling

If Windows left the volume in a dirty state (Fast Startup, hibernation), the mount may be read-only or fail. Disable Fast Startup in Windows (Phase 1 does this) and fully shut down Windows before mounting from Linux. Run `ntfsfix` only as a last resort.

## Windows C: workflow

Games go directly onto the Windows C: volume as a whole; no subfolder is created. Write test files at the C: root to validate the path. Do not create a `Games` directory or games ACL.

## Security considerations

Windows malware can write files that Linux later reads; the mount is read/write and is not isolated. Do not imply isolation. Physical access and local admin can bypass these controls.

## Limitations

- Manual procedure; not automated in Phase 1.
- Not validated on the current machine.
- Device/UUID mapping is machine-specific.

## Future automation

Future hardening may use `noexec,nosuid,nodev` and a tighter `umask`. Phase 2 may automate mounting and transfer.<|end|><|start|>write to=assistant<|channel|>analysis<|message|>Wrote file successfully: docs/windows/file-transfer.md