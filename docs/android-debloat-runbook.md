# Android D0 debloat runbook (Phase D0)

Operator procedure. Run it by hand with the phone attached; an agent must not execute `apply`, `promote`, or `rollback`.

## Contents

- [Invariants](#invariants)
- [Command semantics (read this before trusting `check`/`verify`)](#command-semantics-read-this-before-trusting-checkverify)
- [Preconditions](#preconditions)
- [Batch table](#batch-table)
- [Per-batch procedure](#per-batch-procedure)
- [Feature checkpoints](#feature-checkpoints)
- [Tier-2 promotion](#tier-2-promotion)
- [Final enrollment (after D0)](#final-enrollment-after-d0)
- [Rollback](#rollback)
- [Troubleshooting](#troubleshooting)
- [Risk notes](#risk-notes)

Self-contained. Does not depend on the gitignored planning tree. Applies only to fingerprint `Infinix/X689-GL/Infinix-X689:11/RP1A.200720.011/230416V653:user/release-keys`.

## Invariants

- Allowlist only: nothing removed unless listed in `etc/mdm/policy/debloat.json`.
- Critical denylist refused unconditionally (SystemUI, Settings, PackageInstaller, permission controllers, telephony/IMS/RIL, Bluetooth, NFC, keychain, shell, external storage, MTP, managed provisioning, current home/IME/dialer/SMS handler, any device admin).
- Tier 3 never acted on. Tier 2 disable-first, promote only after a clean reboot.
- `no_safe_boot` is set → no safe mode; a boot wedge costs a factory reset + re-enroll. May be cleared by a factory reset — re-check after OOBE.
- Do not touch `no_safe_boot`, OTA (`com.transsion.systemupdate`), Play (`com.android.vending`), LocalSend (`org.localsend.localsend_app`), Denet (`pro.denet.storage`), `com.google.android.ims`, `com.scorpio.securitycom`.
- **Pre-enrollment mode:** debloat runs before HMDM enrollment. The HMDM agent/console is absent, so Tier 3 keeps (HMDM seed apps, LocalSend, Denet) may be missing; `debloat check` ignores Tier-3 presence. Post-enrollment batches require agent green.

## Command semantics (read this before trusting `check`/`verify`)

- `debloat check` — **pre-flight safety gate**: fingerprint match, manifest schema, critical/denylist intersection, runtime device-admin intersection, role refusals, batch structure, single IME. Package *presence* is advisory (printed as `WARN`, never an error) because presence is a lifecycle fact, not a safety fact: Tier 1's goal state is absence, so an absent Tier 1 is either already removed or absent from this ROM variant — both fine. Tier 2 also ends in removal (disable → promote → uninstall), so the same applies.
- `debloat verify` — **post-state gate**: Tier 1 absent, Tier 2 present+disabled, refused roles held, single IME. This is the command that confirms apps from a batch were actually removed. `verify --batch N` scopes to one batch; `verify` (no arg) covers all.
- `debloat apply` is **idempotent**: entries already absent are skipped (`skip`), recorded in `applied.tsv`, and never written to `restore.sh` (nothing to undo). Re-running a batch converges state.
- `verify`'s Tier 2 rule assumes the **disable-first** stage. This cycle defers `promote`, so Tier 2 stays present+disabled. If `promote` is ever run, revisit verify's Tier 2 expectation (absent post-promote) — deferred work.

## Preconditions

1. `adb devices -l` shows one device. If empty but USB enumerates, run `adb kill-server && adb start-server`, re-seat cable, unlock phone.
2. Phone unlocked, battery adequate.
3. Pre-enrollment mode accepted: agent may be absent; verify ADB device + fingerprint instead. (Post-enrollment batches require agent green.)
4. `debloat check` exits 0 (fingerprint + role + manifest validation).
5. `debloat plan` exits 0 and prints one line per Tier 1 + Tier 2 entry, each tagged `[batch N]`.

## Batch table

| batch | focus |
|---|---|
| 0 | canary (YouTube) |
| 1 | all Tier 1 except canary and Chrome (64) |
| 2 | all Tier 2, disable-first (29) |
| 3 | Chrome (alone, last) |

Per-batch package lists: `debloat plan --batch N`.

## Per-batch procedure

1. `debloat plan --batch N` — review the list.
2. `debloat apply --batch N --yes` (or interactive) — executes one batch, snapshots first.
3. `debloat verify --batch N` — tier 1 absent, tier 2 disabled, roles unchanged.
4. Manual checks: calls, SMS, fingerprint unlock, screenshot, Settings, camera as applicable. (Post-enrollment batches also require agent green in console.)
5. Only then proceed to the next batch.

No mandatory reboot between batches — reboot and re-check calls/SMS after batch 1 if anything looks off.

On failure: `debloat apply` stops at the first failing package and prints `bash <snapshot>/restore.sh`.

## Feature checkpoints

- After `com.fpsensor.fpSensorExtensionSvc2` (batch 2): verify fingerprint unlock.
- After `com.transsion.screencapture` (batch 2): verify hardware screenshot.
- After `com.transsion.ossettingsext` (batch 2): verify Settings opens.
- After gallery removal (batch 1): verify camera still works.
- Throughout: calls + SMS.

## Tier-2 promotion

Deferred during this end-to-end reorder test. Tier 2 stays disabled (accepted D0 end state). If ever run, only after a clean reboot following batch 2:

```
debloat promote --batch 2 --confirm-clean-reboot --yes
```

Promotes disabled Tier-2 entries to `pm uninstall --user 0`.

## Final enrollment (after D0)

1. `python3 lib/66-mdm-baseline.py --verify` (exit 0) — baseline gate before any device step.
2. `python3 lib/67-mdm-provision.py --ensure-channel` (exit 0; both forwards green).
3. `python3 lib/67-mdm-provision.py --ensure-device` (exit 0; admin password only if prompted).
4. `python3 lib/67-mdm-provision.py --enroll` (owner confirms prompts; poll gate green ≤120s). This syncs `policy.json` to the server configuration before the later device-owner gates / device-owner handoff (earlier phone-side steps — APK staging, channel setup/apply, and init-payload acquisition — precede the sync).
5. `python3 lib/67-mdm-provision.py --verify` (exit 0) + `debloat check`/`verify` (exit 0).
6. Console proof: device last-sync fresh; managed launcher pinned; removed whitelisted apps stay absent.

If a policy-sync step fails, establish whether it stopped before or after writes (`_apply_links` / restrictions PUT) before deciding on rollback — a failure at the read-only `_catalog` GET needs none. Once the fix passes acceptance verification, re-run step 1 (expect 0), then step 4 as `python3 lib/67-mdm-provision.py --enroll` (prompts for admin login and password). If the device row or cached QR key has since been removed, run step 3 (`--ensure-device`) first. A failure after writes must not be assumed read-only.

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

- Launcher Settings-block / 3-minute permissive-window interaction unverified.
- Vendor internals (`hamal`, `nephilim`, `tabe`, `permissionprotect`, `overlaysuw`, `ossettingsext`, `antitheft`) have no public docs; behaviour after removal unknown.
