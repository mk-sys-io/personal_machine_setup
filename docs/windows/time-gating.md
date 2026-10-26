# Time-gating Windows accessibility (Phase 3 research)
> Status: Future — Phase 3 · Not implemented

Control when Windows is accessible/bootable during the day. Layered approach from hardware-level to OS-level. This is research only; no gating is active on the current machine.

## Layers

### 1. Cold power-on (hardware level)

- **BIOS/UEFI RTC alarm:** Machine powers on at a configured time independent of OS. Coarse granularity (often daily/weekly fixed time). Set in firmware UI or via `efivar`/vendor tools.
- **Smart plug:** Schedule hard power on/off. Simplest but coarsest. Risk: hard power cuts can corrupt filesystems.
- **Wake-on-LAN (WoL):** Debian sends a magic packet to wake the Windows machine. Requires NIC + BIOS support + soft-off (S5) with WoL enabled.

### 2. Boot gating (GRUB level)

GRUB cannot boot Windows directly; it chainloads it. Time-based gating uses:

- **`datehook` module:** Populates `YEAR`, `MONTH`, `DAY`, `HOUR`, `MINUTE`, `SECOND`, `WEEKDAY` environment variables at boot. Read from RTC.
- **Hidden timeout:** `GRUB_TIMEOUT_STYLE=hidden` with `GRUB_TIMEOUT=0` skips menu unless Shift is held.
- **Password protection:** `password_pbkdf2` + `superusers` + `--users` on menuentry. Prevents unauthorized boot selection.
- **Custom `grub.cfg`:** Hand-written time-conditional logic using `datehook` variables. `grub-mkconfig` does not generate this automatically.

**Env-block storage limits:** GRUB env block only works on plain disk (no LVM/RAID), non-checksumming filesystems (no ZFS), BIOS/EFI functions (no ATA/USB/IEEE1275). Btrfs uses reserved header area.

### 3. Linux-side wake (when Linux is running)

- **systemd timers:** `OnCalendar=` scheduling with `WakeSystem=true` arms RTC alarm to wake from suspend. `Persistent=true` runs missed triggers.
- **rtcwake:** util-linux tool to set RTC alarm and enter sleep state. Modes: standby (S1), mem (S3), disk (S4), off (S5). Reads UTC/local from `/etc/adjtime`.

### 4. In-Windows tasks (when Windows is running)

- **Windows Task Scheduler:** Schedule triggers with "Wake the computer to run this task" (wake timers). Reliable within Windows but cannot cold-boot a powered-off machine.

## Gotchas

- **RTC drift:** NTP syncs system clock; RTC drifts unless `hwclock --systohc`. Alarms drift with it.
- **DST:** RTC usually UTC; local-time RTC + DST = doubled/missed alarms.
- **Dual-boot clock skew:** Windows writes local time to RTC by default; Linux assumes UTC → skew affecting rtcwake and GRUB `datehook`. Fix: `RealTimeIsUniversal` registry on Windows or set Linux to local time.
- **Fast Startup:** Hybrid hibernation leaves NTFS hibernated; Linux read-write mount risks corruption. It must be disabled; Phase 1 does this.
- **BitLocker recovery:** GRUB/Secure Boot/TPM state changes can trigger BitLocker recovery. Chainloading alters measured boot (TPM PCRs).
- **Physical override:** Physical access (USB boot, firmware boot menu, disk removal) bypasses GRUB password. Need firmware password + Secure Boot + chassis lock for real lockdown.
- **Hardware variance:** RTC alarm range, S3/S4, WoL, BIOS scheduled power-on all vary by board. Test per machine.
- **GRUB `datehook` accuracy:** Reads RTC at boot; accuracy = RTC accuracy.

## Complexity

| Approach | Complexity | Notes |
|----------|-----------|-------|
| Smart plug + WoL | Low | Simplest, coarsest |
| BIOS RTC alarm | Low | Set once in firmware |
| rtcwake (Linux suspend/wake) | Medium | Hardware-dependent |
| systemd timer + WakeSystem | Medium | Robust scheduling + missed-run handling |
| GRUB date-based gating | High | Custom `grub.cfg`, env-block limits, BitLocker/TPM interactions |
| Full lockdown (firmware password + Secure Boot + chassis lock) | High | Required for real override protection |

## Feasibility

Feasible as a layered scheme. Reliability is moderate. Main risks: clock/DST skew, Fast Startup, BitLocker recovery triggers, env-block storage limits, per-board hardware variance. Physical-access override remains the fundamental limit of GRUB-level gating.