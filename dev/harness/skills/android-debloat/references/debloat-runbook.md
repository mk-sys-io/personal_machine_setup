# Android pre-lockdown debloat runbook (Phase D0)

Self-contained. Does not depend on the gitignored planning tree. Applies only to fingerprint `Infinix/X689-GL/Infinix-X689:11/RP1A.200720.011/230416V653:user/release-keys`.

## Invariants

- Allowlist only: nothing removed unless listed in `etc/mdm/policy/debloat.json`.
- Critical denylist refused unconditionally (SystemUI, Settings, PackageInstaller, permission controllers, telephony/IMS/RIL, Bluetooth, NFC, keychain, shell, external storage, MTP, managed provisioning, current home/IME/dialer/SMS handler, any device admin).
- Tier 3 never acted on. Tier 2 disable-first, promote only after a clean reboot.
- `no_safe_boot` is set → no safe mode; a boot wedge costs a factory reset + re-enroll. May be cleared by a factory reset — re-check after OOBE.
- Do not touch `no_safe_boot`, OTA (`com.transsion.systemupdate`), Play (`com.android.vending`), LocalSend (`org.localsend.localsend_app`), Denet (`pro.denet.storage`), `com.google.android.ims`, `com.scorpio.securitycom`.
- **Pre-enrollment mode:** debloat runs before HMDM enrollment. The HMDM agent/console is absent, so Tier 3 keeps (HMDM seed apps, LocalSend, Denet) may be missing; `debloat check` ignores Tier-3 presence. Post-enrollment batches require agent green.

## Preconditions

1. `adb devices -l` shows one device. If empty but USB enumerates, run `adb kill-server && adb start-server`, re-seat cable, unlock phone.
2. Phone unlocked, battery adequate.
3. Pre-enrollment mode accepted: agent may be absent; verify ADB device + fingerprint instead. (Post-enrollment batches require agent green.)
4. `debloat check` exits 0 (fingerprint + role + manifest validation).
5. `debloat plan` prints 96 lines (67 Tier 1 + 29 Tier 2).

## Batch table (package counts)

| batch | n | focus |
|---|---|---|
| 0 | 1 | canary (YouTube) |
| 1 | 11 | Google media/entertainment |
| 2 | 21 | Transsion bloat |
| 3 | 17 | Facebook / OEM / MediaTek leftovers |
| 4 | 16 | wallpaper / AOSP leftovers |
| 5 | 14 | Google app + Transsion Tier 2 |
| 6 | 15 | UAD-Expert Tier 1 demotions |
| 7 | 1 | Chrome (last) |

## Per-batch procedure

1. `debloat plan --batch N` — review the list.
2. `debloat apply --batch N --yes` (or interactive) — executes one batch, snapshots first.
3. `debloat verify --batch N` — tier 1 absent, tier 2 disabled, roles unchanged.
4. Reboot the phone.
5. Manual checks: calls, SMS, fingerprint unlock, screenshot, Settings, camera as applicable. (Post-enrollment batches also require agent green in console.)
6. Only then proceed to the next batch.

On failure: `debloat apply` stops at the first failing package and prints `bash <snapshot>/restore.sh`.

## Feature checkpoints

- After `com.fpsensor.fpSensorExtensionSvc2` (batch 6): verify fingerprint unlock.
- After `com.transsion.screencapture` (batch 5): verify hardware screenshot.
- After `com.transsion.ossettingsext` (batch 5): verify Settings opens.
- After gallery removal (batch 1): verify camera still works.
- Throughout: calls + SMS.

## Tier-2 promotion

Deferred during this end-to-end reorder test. Tier 2 stays disabled (accepted D0 end state). If ever run, only after a clean reboot following the batch that disabled them:

```
debloat promote --batch N --confirm-clean-reboot --yes
```

Promotes disabled Tier-2 entries to `pm uninstall --user 0`.

## Final enrollment (after D0)

1. `python3 lib/67-mdm-provision.py --ensure-channel` (exit 0; both forwards green).
2. `python3 lib/67-mdm-provision.py --ensure-device` (exit 0; admin password only if prompted).
3. `python3 lib/67-mdm-provision.py --enroll` (owner confirms prompts; poll gate green ≤120s).
4. `python3 lib/67-mdm-provision.py --verify` (exit 0) + `debloat check`/`verify` (exit 0).
5. Console proof: device last-sync fresh; managed launcher pinned; removed whitelisted apps stay absent.

## Rollback

1. `bash ~/.local/state/mdm/debloat/<ts>/restore.sh [serial]` — runs `cmd package install-existing --user 0` / `pm enable`.
2. `debloat rollback --last --yes` (fingerprint-guarded).
3. Last resort: factory reset → re-enroll (Path A, rehearsed 2026-11-16). ADB stays on (D13).

## Troubleshooting

- `DELETE_FAILED_OWNER_BLOCKED` / `DELETE_FAILED_DEVICE_POLICY_MANAGER`: DO is blocking; stop and reassess.
- adb flapping (USB enumerated, `adb devices` empty): `adb kill-server && adb start-server`, re-seat cable, unlock phone.
- `pm uninstall --help` returns `Error: Unknown option: --help` on this build; use `pm help`.
- `cmd package install-existing` is present (AOSP 8.0+); rollback viable.
- YouTube rollback restores the `/product` base, not the `/data` update.
- `com.google.android.ims` is Carrier Services/RCS, already disabled; never touch.

## Risk notes

- No safe mode (`no_safe_boot`); boot wedge → factory reset + re-enroll.
- Launcher Settings-block / 3-minute permissive-window interaction unverified.
- Vendor internals (`hamal`, `nephilim`, `tabe`, `permissionprotect`, `overlaysuw`, `ossettingsext`, `antitheft`) have no public docs; behaviour after removal unknown.
