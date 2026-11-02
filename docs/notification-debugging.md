# Notification Debugging — Temporary Diagnostic System

**Status:** scaffold (Phase 0) · Created 2026-11-01 · Updated 2026-11-02
**Lifetime:** temporary, except the single permanent observer named in §8.
**Branch:** `diag/notification-logging`

## Summary

This document describes a **temporary** notification debugging system for the sway/Wayland desktop. Its purpose is to answer, with evidence: was a notification sent, did it reach the daemon, was it suppressed or dropped, and did it fire at the expected time. The system is deliberately disposable — only one slim passive observer survives dismantling (§8/§9).

Verified facts this design rests on (2026-11-02):

- `notify-send` 0.8.6: `-p`, `-r`, `-t` work (used for daemon-assigned ID capture).
- `swaync` 0.11.0: does **not** emit `NotificationClosed` on popup expiry (`notiDaemon.vala`); its `swaync-client -s` man page's "add and close events" claim is misleading — only aggregate frames (`{count, dnd, visible, inhibited}`) arrive, change-driven.
- The reference D-Bus monitor (`BecomeMonitor`) sees the `Notify` call but never the reply and cannot send messages.
- The systemd user manager carries `DBUS_SESSION_BUS_ADDRESS` natively (`systemctl --user show-environment`).
- Journal is persistent (`/var/log/journal`, spans boots) — suspend/resume, timesyncd, timer catch-up, service lifecycle are recoverable on demand.
- `dotfiles/sway/config:82` uses `exec_always $config_dir/scripts/autostart.sh`, so units come up on sway reload/reboot.

## Components

| Component | File / unit | Role | Lifetime |
|---|---|---|---|
| `notify-log-send` | `tools/notify-log-send` (flat file, deploys unchanged via `make tools`) | wraps `/usr/bin/notify-send`; logs `attempt` + `send_result` (exit code, latency); captures daemon ID via `notify-send … -p` | temporary |
| D-Bus observer | systemd user unit (Python, `BecomeMonitor`) | logs `Notify`, `NotificationClosed`, `ActionInvoked`, daemon owner changes | temporary |
| swaync subscriber | systemd user unit (`swaync-client -s`) | aggregate state frames only — as-of DND/cc/visibility for reconciler joins | temporary |
| Reconciler + timer | systemd user units | joins attempt ↔ observation; emits `occurrence` for rare anomalies | temporary |
| `notify-health.service` | systemd user unit, slim passive bus observer | sole permanent component — writes `kind:"health"` metadata-only records | **permanent** |
| Raw day files | `~/.local/state/notify-debug/YYYY-MM-DD.jsonl` | full-content temporary logs | temporary |
| `plans/` artifacts | `plans/active/notification-logging/*` | scratch planning | temporary |

No standalone snapshot collector: everything recoverable later is read from journalctl on demand (§6). Only unrecoverable-at-capture data is inlined in `attempt` records (condition inputs, env vars, PID chain).

## Data flow

```
producer ──► notify-log-send ──► attempt (env + PID chain + trigger + expected_at)
              │                      + notify-send … -p ──► D-Bus
              └──► send_result (exit code, latency, daemon ID)
                                         │
                                         ▼
                          D-Bus observer ──► observed_notify / observed_close / daemon_owner
                          swaync subscriber ──► swaync_state (aggregate frames)
                                         │
                                         ▼
                          ~/.local/state/notify-debug/YYYY-MM-DD.jsonl
                                         │
                                         ▼
                          reconciler ──► ok / missing_fire / unknown_fire / not_shown
                                         / wrong_time / duplicate / id_anomaly / stale_reason
                                         + rare occurrence-index records (kind:"occurrence")
```

`swaync-log-id` hint is the only intent↔observation correlation channel — the monitor sees `Notify` but never the reply, and cannot send messages. Reconciler stitches day boundaries (attempt 23:59:59 → observed 00:00:01).

## Correlation ID and visibility rules

- Each `attempt` carries `event_id` hint `swaync-log-id`, propagated as the correlation key.
- Reconciler joins against `observed_notify` + `swaync_state` (as-of DND/cc/inhibited), not close signals — swaync emits no `NotificationClosed` on expiry.
- Reconciler must model swaync daemon-side `notification-visibility` rules (`dotfiles/sway/swaync/config.json:41–64`) or transient producers read as false `not_shown`.
- `wrong_time` = `observed_at − attempt.trigger.expected_at` (scheduled sends supply `expected_at`).
- `wrong_time`/`missing_fire` diagnosis cross-reads journal via `boot_id` windows (suspend/resume, timesyncd catch-up, timer catch-up).

## Log structure

One JSONL per day at `~/.local/state/notify-debug/YYYY-MM-DD.jsonl`. Fixed envelope:

```
{ts, rt, mono, boot_id, kind, src, app, event_id, …}
```

- `kind ∈ {attempt, send_result, observed_notify, observed_close, swaync_state, daemon_owner, occurrence, health}`
- `rt` = `CLOCK_REALTIME`, `mono` = `CLOCK_MONOTONIC`, `boot_id` = kernel boot ID for journal cross-reference.
- `health` records are written to the separate permanent stream `~/.local/state/notify-health.jsonl` (metadata only: app_name, ts, fired/blocked, DND-state-at-fire), produced by `notify-health.service` — the sole surviving component.

## Instrumented call sites

Phase 2 scope: every code path calling `notify-send` — 10 sway producers plus the root-invoked rkhunter hook:

- `dotfiles/sway/scripts/{changevolume, clipboard-toast.py, battery-alert.sh, screenshot, changebrightness, graceful-close.sh, wlsunset-notify.py, bluetooth-idle-monitor.sh, sys-alert}`
- `dotfiles/sway/scripts/autostart.sh`
- `etc/security/rkhunter/notify-user.sh` (deployed to `/usr/local/bin/notify-user` by `lib/55-security.sh:121–129`)

Each producer supplies a `trigger` block (mode: scheduled|condition|manual; invoker: systemd-timer|sway-keybind|boot|script-chain; unit; ppid_chain) plus `expected_at` for scheduled sends. Suppressed decisions (`cooldown`, `threshold`, `no-device`) logged as `decision=suppressed`. Env diagnostics (`DBUS_SESSION_BUS_ADDRESS`, `DISPLAY`, `WAYLAND_DISPLAY`, `SWAYSOCK`, `XDG_RUNTIME_DIR`, PID) nested inside `attempt`.

Espanso is excluded — notifications disabled at source.

Fallback: producers use `/usr/bin/notify-send` directly if the helper is unavailable.

## Privacy and retention

- Logs contain metadata + environment diagnostics only; no notification body content beyond what producers already emit as `app`/`src`.
- `notify-debug/` day files: temporary-by-disposition, deleted at dismantling, no time-based cleanup beforehand, size-based rotation only if disk pressure appears.
- `notify-health.jsonl`: indefinite retention (permanent).
- Snapshot-collector dropped: journal is persistent; everything recoverable is referenced by `boot_id`, never copied.

## Component disposition (Decision b — written at scaffold time)

| Disposition | Components |
|---|---|
| Temporary | Instrumentation call sites, `notify-log-send` helper, D-Bus observer unit, swaync subscriber unit, reconciler + timer units, raw `notify-debug/` day files, `plans/` artifacts |
| Permanent | `notify-health.service` + `notify-health.jsonl` (one slim passive bus observer + its metadata-only log) |

## Dismantling (documented now, executed later — Phase 5)

1. Revert the Phase 2 instrumentation commit (single commit → single revert).
2. Drop observer/subscriber/reconciler units; `make dotfiles` + `make tools`.
3. Delete `~/.local/state/notify-debug/*.jsonl`; leave `notify-health.jsonl` untouched.
4. Capability loss: `missing_fire`, `wrong_time`, suppressed-decision diagnosis die with the temporary system.
5. Tag the final commit `diag/notification-logging-final` as the reactivation anchor (tag, not a stale branch).

## Archive / retrieval

- Wrong-time diagnosis: correlate `attempt` vs `observed_notify` deltas against journalctl windows (`_BOOT_ID=<…>`, `--since/--until`) for suspend/resume, timesyncd, timer catch-up; read the `occurrence` index; map delta class → cause.
- Reactivation path: checkout tag `diag/notification-logging-final` and re-apply; documented fully in Phase 5.

## Verified machine versions

notify-send 0.8.6 (`-p`, `-r`, `-t` work), swaync 0.11.0, reference `dbus-daemon`, persistent journal (2026-11-02).