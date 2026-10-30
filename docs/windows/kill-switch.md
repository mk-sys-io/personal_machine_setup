# Kill switch (Phase 2 design)
> Status: Future — Phase 2 · Not implemented

## Goal

Design note for a future Linux-controlled network kill switch on the Windows secondary OS. Not implemented in Phase 1.

## Mechanism

Boot-time only. Linux writes a flag file on the Windows NTFS volume; a Windows scheduled task reads it at boot and enables/disables networking. Not real-time; change takes effect on next boot.

## Path mapping

`/mnt/windows/killswitch.flag` (Linux) maps to `C:\killswitch.flag` (Windows). Consistent path across both sides; same NTFS partition, same relative location.

## Windows boot task

A scheduled task at boot (SYSTEM, highest privileges) reads `C:\killswitch.flag` and enables or disables network adapters / outbound firewall rules accordingly.

## Defaults and failure mode

Default on first boot is an open decision. Missing or unreadable flag must fail safe (deny or allow — TBD). Not tamper-proof: physical access and local admin can bypass.

## Limitations

- Boot-time only; not real-time.
- Windows must be fully shut down (not hibernated) for Linux to write the flag cleanly.
- Depends on `file-transfer.md` (NTFS mount) and Fast Startup disabled (Phase 1 does this).
- Physical access and local admin can bypass.

## Open decisions

- Default allow/deny on first boot.
- Adapter disable vs firewall block.

## Future enhancements

- Real-time control channel (out of Phase 2 scope).
- Authenticated flag writes.