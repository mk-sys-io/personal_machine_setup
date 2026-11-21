---
name: android-debloat
description: Load before editing the debloat manifest, running tools/debloat.py, or changing Android packages over ADB.
---

# Android pre-lockdown debloat (Phase D0)

The only allowlist is `etc/mdm/policy/debloat.json` (129 entries: 67 Tier 1, 29 Tier 2, 33 Tier 3). The runner is `tools/debloat.py` (deployed as `debloat`).

Rules:

- `plan` is the default mode; `apply` is the only mutating subcommand and requires owner go.
- One batch per `apply` (batches 0–7). Never batch multiple batches into one apply.
- Tier 3 entries are informational and never acted on.
- Fingerprint and role checks are binding: abort on mismatch, refuse HOME/IME/DIALER/SMS/EMERGENCY holders, and only allow `role_override` for SYSTEM_GALLERY / BROWSER / ASSISTANT.
- Tier 2 items are disable-first; promote to uninstall only after a clean reboot.
- Never vendor the UAD dataset; consume only the local cache at `~/.cache/uad/uad_lists.json`.
- After each batch: reboot → calls + SMS work → (post-enrollment only) agent green in console → feature checks → next batch. Pre-enrollment: ADB + boot + calls/SMS suffice.
- Read [the debloat runbook](references/debloat-runbook.md) before any apply.

Supporting files: `references/debloat-runbook.md` (preconditions, batch table, per-batch procedure, feature checkpoints, promotion, rollback, troubleshooting).