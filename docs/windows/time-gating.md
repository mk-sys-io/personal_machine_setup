# Time gating (Phase 3 research)
> Status: Future — Phase 3 · Research only

## Goal

Research note for daily time-gating of Windows bootability. Not implemented in Phase 1 or Phase 2.

## Layers

- Firmware RTC and boot-time checks.
- Smart plug / WoL for power control.
- GRUB `datehook` for boot-menu gating.
- systemd timers on Debian.
- `rtcwake` for scheduled wake.
- Windows scheduled tasks.

## Dependencies on Phase 1

Phase 1 disables Fast Startup (`HiberbootEnabled=0`), which is a prerequisite for clean time-gating and NTFS access. Phase 1 also resolves the local-RTC convention: Linux keeps local RTC (`Africa/Tunis`, UTC+1, no DST) and Windows enforces `RealTimeIsUniversal=0` with `W. Central Africa Standard Time`. Firmware RTC handling must respect this.

## Gotchas

- Firmware RTC vs OS clock mismatch.
- Smart-plug/WoL reliability.
- GRUB `datehook` limitations.
- systemd timer accuracy across reboots.
- Windows task scheduler drift.

## Complexity

Medium-to-high; multiple interacting layers across firmware, bootloader, OS, and power.

## Feasibility

Feasible but not trivial; requires per-machine tuning and physical-access considerations.

## Open decisions

- Which layer is authoritative (firmware vs OS vs power).
- Bypass handling for physical access.
- Local-admin override policy.

## Future enhancements

- Hardware-backed gating.
- Anti-bypass hardening.

Physical access and local admin bypass remain unsolved. Phase 1 does not achieve offline Windows or time gating.