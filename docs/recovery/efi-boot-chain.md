# efi-boot-chain — ESP integrity after a package upgrade

Scope: the UEFI chain from NVRAM to the kernel, and the guardrail added
after 2026-11-25. Secure Boot is disabled, so nothing downstream of
firmware validates a loader.

## chain

firmware `BootOrder` → RefindPlus NVRAM entry → `\EFI\refind\refind_x64.efi`
→ menu → `\EFI\debian\shimx64.efi` → `\EFI\debian\grubx64.efi` →
`grub.cfg` → kernel/initrd.

Firmware parses the PE image only: no checksum, no re-read. The NVRAM
entry's target is what executes.

## incident 2026-11-25

- Routine `apt upgrade`: `grub-efi-amd64`, `grub-efi-amd64-bin`,
  `grub-efi-amd64-signed`, `shim-signed` trigger; the ESP is rewritten.
- `grub-install` printed `Installation finished. No error reported.` and
  exited 0, but `\EFI\debian\shimx64.efi` ended up 4096 bytes (one FAT
  cluster) instead of 1,036,152.
- Cause: grub-install's copy loop treats a zero-length read/write as EOF
  and does not verify the result; dpkg/apt only see the exit code.
- Symptom: RefindPlus reported `LoadImage failed Unsupported`. Windows
  still booted; recovery needed a live USB.

## guardrail

`/usr/local/sbin/refindplus-assert-bootorder` verifies every
NVRAM-referenced Debian/RefindPlus loader plus a standard set against its
authoritative source by SHA-256 and PE header (`MZ` + `PE\0\0`); a
first-seen digest is recorded when no reference exists (size is one input
to that, not a package check).

Repairs from:

- `/usr/lib/shim/shimx64.efi.signed`
- `/usr/lib/shim/mmx64.efi.signed`
- `/usr/lib/grub/x86_64-efi-signed/grubx64.efi.signed`

State lives in `/var/lib/refindplus/esp-digests`; on failure it touches
`/var/lib/refindplus/verify-failed` and clears it automatically once
verification passes. It never exits non-zero. Force a check (and repair)
manually with `/usr/local/sbin/refindplus-assert-bootorder`.
`refind_x64.efi` has no package source: repair it with
`./install.py --only 62` or from the extracted tarball.

## triggers

- apt `DPkg::Post-Invoke` hook — after every dpkg transaction.
- `refindplus-shutdown-verify.service` — before shutdown/umount.
- `refindplus-esp-verify.timer` — daily.
- `refindplus-bootorder.service` — boot.

Boot-time verification cannot save the boot it runs in: Linux never
starts if the chain is broken. The apt hook and shutdown unit are the
guards that matter.

## recovery

1. Boot live USB.
2. Mount the Debian root.
3. Mount the ESP *inside* the chroot. Classic trap: a chroot without
   `/boot/efi` mounted writes a decoy tree onto the root filesystem and
   silently leaves the real ESP untouched.
4. Copy the loader from the package, e.g.
   `cp /usr/lib/shim/shimx64.efi.signed /boot/efi/EFI/debian/shimx64.efi`.
5. Verify `sha256` against the package file.
6. `sync`, reboot.

## firmware-level redundancy

A second NVRAM entry pointing at a different loader (e.g.
`\EFI\debian\grubx64.efi`) is the only fallback that survives a broken
`refind_x64.efi` or `shimx64.efi`.

## reference

- `lib/62-refindplus.sh`
- `system/systemd/system/refindplus-assert-bootorder.sh`
- `system/apt/apt.conf.d/99-refindplus-bootorder`
