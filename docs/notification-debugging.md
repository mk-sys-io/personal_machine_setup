# Notification Debugging — Temporary Diagnostic System

**Status:** finalized · Created 2026-11-01 · Updated 2026-11-02
**Lifetime:** temporary, except the single permanent observer named in §9.

## Summary

This document describes a **temporary** notification debugging system for the sway/Wayland desktop. Its purpose is to answer, with evidence: was a notification sent, did it reach the daemon, was it suppressed or dropped, and did it fire at the expected time. The system is deliberately disposable — only one slim passive observer survives dismantling (§9/§10).

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

No standalone snapshot collector: everything recoverable later is read from journalctl on demand. Only unrecoverable-at-capture data is inlined in `attempt` records (condition inputs, env vars, PID chain).

## Setup and activation

Deploy (from a clean checkout of the tag `diag/notification-logging-final`, or `main` once merged):

1. `make tools` — copies `tools/*` to `~/.local/bin/`, stripping the extension, so `tools/notify-observe.py` becomes `~/.local/bin/notify-observe` (same for the other four helpers; `tools/notify-log-send` is a flat file and deploys unchanged).
2. `make dotfiles` — deploys `dotfiles/systemd/user/notify-*.service`/`*.timer` into `~/.config/systemd/user/` (the `systemd/` app dir is part of the app loop; `clean-stale` never sweeps `*.service`/`*.timer`).
3. Reload sway (or reboot). `dotfiles/sway/config:82` runs `autostart.sh` via `exec_always`, so the marked activation block re-runs on every reload:

   ```
   ## notification-logging — temporary debug system (see docs/notification-debugging.md §9)
   mkdir -p "$HOME/.local/state"
   systemctl --user daemon-reload >/dev/null 2>&1 || true
   for _nu in notify-observe.service notify-swaync-sub.service notify-health.service notify-reconcile.timer; do
       systemctl --user enable --now "$_nu" 2>>"$HOME/.local/state/notify-startup.log" || true
   done
   ```

   `daemon-reload` is required before the first enable (units may have changed since last boot). Per-unit stderr lands in `~/.local/state/notify-startup.log` — the only place an activation failure is visible, since the `|| true` swallows non-zero exits.
4. Verify: `systemd-analyze --user verify dotfiles/systemd/user/notify-*.service dotfiles/systemd/user/notify-*.timer`; `systemctl --user is-active notify-observe.service notify-swaync-sub.service notify-health.service notify-reconcile.timer` (all `active`); first records land in `~/.local/state/notify-debug/` and `~/.local/state/notify-health.jsonl`.

`notify-reconcile.service` is `Type=oneshot` with no `[Install]` — it is never enabled directly; the timer drives it (`OnBootSec=120`, `OnUnitActiveSec=300`).

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
- `wrong_time`/`missing_fire` diagnosis cross-reads the journal via `boot_id` windows (suspend/resume, timesyncd catch-up, timer catch-up). Thresholds live in `tools/notify-reconcile.py` (`WRONG_TIME_THRESHOLD = 5.0 s`, `GRACE_SECONDS = 60.0 s`). The cross-read is always manual - no tool invokes journalctl - use `journalctl --user _BOOT_ID=<boot_id> --since "@<rt-120>" --until "@<rt+120>" --no-pager` (`@<epoch>` accepts `CLOCK_REALTIME` seconds, which is exactly the `rt` field, so the log timestamps paste straight in).

## Log structure

One JSONL per day at `~/.local/state/notify-debug/YYYY-MM-DD.jsonl`. Fixed envelope:

```
{ts, rt, mono, boot_id, kind, src, app, event_id, …}
```

- `kind ∈ {attempt, send_result, observed_notify, observed_close, swaync_state, daemon_owner, occurrence, health}`
- `rt` = `CLOCK_REALTIME`, `mono` = `CLOCK_MONOTONIC`, `boot_id` = kernel boot ID for journal cross-reference.
- `health` records are written to the separate permanent stream `~/.local/state/notify-health.jsonl` (metadata only: app_name, ts, fired/blocked, DND-state-at-fire), produced by `notify-health.service` — the sole surviving component.

## Instrumented call sites

Instrumentation scope: every code path calling `notify-send` — 10 sway producers plus the root-invoked rkhunter hook:

- `dotfiles/sway/scripts/{changevolume, clipboard-toast.py, battery-alert.sh, screenshot, changebrightness, graceful-close.sh, wlsunset-notify.py, bluetooth-idle-monitor.sh, sys-alert}`
- `dotfiles/sway/scripts/autostart.sh`
- `etc/security/rkhunter/notify-user.sh` (deployed to `/usr/local/bin/notify-user` by `lib/55-security.sh:121–129`)

Each producer supplies a `trigger` block (mode: scheduled|condition|manual; invoker: systemd-timer|sway-keybind|boot|script-chain; unit; ppid_chain) plus `expected_at` for scheduled sends. Suppressed decisions are logged as `decision=suppressed` with a `reason` field — the only two reasons actually emitted in code are `cooldown` (`sys-alert:37`) and `no-device` (`battery-alert.sh:19`, `bluetooth-idle-monitor.sh:14,21`); the earlier `threshold` mention was aspirational and is not implemented. Env diagnostics (`DBUS_SESSION_BUS_ADDRESS`, `DISPLAY`, `WAYLAND_DISPLAY`, `SWAYSOCK`, `XDG_RUNTIME_DIR`, PID) nested inside `attempt`.

Espanso is excluded — notifications disabled at source.

Fallback: producers use `/usr/bin/notify-send` directly if the helper is unavailable. **Systemd-invoked producers must resolve the helper by absolute path (`$HOME/.local/bin/notify-log-send`), not `command -v`: the systemd user manager's PATH does not include `~/.local/bin`, so a `command -v` check always fails and silently degrades to the fallback, which ignores `NLOG_SUPPRESS` (root cause of the "no radio present" popup).**

## Privacy and retention

- **Temporary day files (`notify-debug/`) are full-content logs, not metadata-only.** `observed_notify` records carry the notification `summary` and `body` verbatim plus the full hint dict (including `swaync-log-id` and sender PID); `unknown_fire` occurrence records carry the unmatched `summary`; `missing_fire` occurrence records embed the entire `attempt` record, including env diagnostics and the PPID chain. Treat a day file like a capture of everything that crossed the notification bus while the system was running.
- **Permanent health stream (`notify-health.jsonl`) is metadata-only** and stays that way: `{ts, rt, mono, boot_id, kind, src, app, app_name, event_id, fired, blocked, dnd_at_fire, inhibited_at_fire}` — app name, timestamp, fired/blocked, DND-state-at-fire. No summary, body, env, or PID data. Indefinite retention.
- `notify-debug/` day files: temporary-by-disposition, deleted at dismantling, no time-based cleanup beforehand, size-based rotation only if disk pressure appears.
- Snapshot-collector dropped: journal is persistent; everything recoverable is referenced by `boot_id`, never copied.

## Component disposition (temporary vs permanent)

| Disposition | Components |
|---|---|
| Temporary | Instrumentation call sites, `notify-log-send` helper, D-Bus observer unit, swaync subscriber unit, reconciler + timer units, raw `notify-debug/` day files |
| Permanent | `notify-health.service` + `notify-health.jsonl` (one slim passive bus observer + its metadata-only log) |

## Dismantling (documented now, executed later)

Preconditions (all must hold before executing):

- The branch has been merged into `main` with a **true merge** (not a squash). A squash would leave the 5 original commits reachable only through the branch ref/reflog; `git branch -d` would then orphan them. With a true merge they live in `main`'s history and the branch can be deleted safely.
- Tag `diag/notification-logging-final` exists (created at finalization close) and is ideally pushed to `origin`.
- Sway is not mid-reload (the activation block re-runs on every reload).

What survives: only `notify-health.service` + `~/.local/state/notify-health.jsonl`. Everything else is removed, and the instrumented source stays archived in git history under the tag and the merge commit (retrieve any path with `git show diag/notification-logging-final:<path>` or by checking the tag out in a worktree).

Execution steps (run in order):

1. Disable and stop the temporary units (keep `notify-health.service`):

   ```
   systemctl --user disable --now notify-observe.service notify-swaync-sub.service notify-reconcile.timer
   systemctl --user stop notify-reconcile.service 2>/dev/null || true
   ```

2. Revert the instrumentation commit. It is a single commit, so it is a single revert:

   ```
   git revert bb8c8bf
   ```

   Expected to apply cleanly: only one instrumentation-era file (`dotfiles/sway/scripts/autostart.sh`) was touched again later (the service-wiring commit `f688b75`), and the hunks are disjoint (instrumentation edited the helper shim near the top and the system-monitors send line; service-wiring appended the enable loop). `tools/notify-log-send` was not touched after instrumentation. If a conflict does appear, resolve by keeping the service-wiring enable loop and dropping the instrumentation shim + call-site edits.
3. Remove the activation block from `dotfiles/sway/scripts/autostart.sh` (lines 119–126). This block belongs to the service-wiring commit (`f688b75`), not to the instrumentation revert, so step 2 does not touch it. It must be deleted explicitly — `autostart.sh` is `exec_always` and re-runs the block on every sway reload, which would re-enable the units even after step 1:

   ```
   ## notification-logging — temporary debug system (see docs/notification-debugging.md §9)
   ## Re-run on every sway reload (autostart is exec_always) so units follow repo state.
   ## notify-health.service is the sole permanent component; the other three are temporary.
   mkdir -p "$HOME/.local/state"
   systemctl --user daemon-reload >/dev/null 2>&1 || true
   for _nu in notify-observe.service notify-swaync-sub.service notify-health.service notify-reconcile.timer; do
       systemctl --user enable --now "$_nu" 2>>"$HOME/.local/state/notify-startup.log" || true
   done
   ```

4. Remove the temporary unit files and reload the user manager. `clean-stale` never sweeps `*.service`/`*.timer` (`lib/helpers/clean_stale.sh:80`), so this is explicit:

   ```
   rm -f ~/.config/systemd/user/notify-observe.service ~/.config/systemd/user/notify-swaync-sub.service \
         ~/.config/systemd/user/notify-reconcile.service ~/.config/systemd/user/notify-reconcile.timer
   rm -f dotfiles/systemd/user/notify-observe.service dotfiles/systemd/user/notify-swaync-sub.service \
         dotfiles/systemd/user/notify-reconcile.service dotfiles/systemd/user/notify-reconcile.timer
   systemctl --user daemon-reload
   ```

   The four temporary helper binaries (`notify-observe`, `notify-swaync-sub`, `notify-reconcile`, `notify-log-send`) are swept automatically by `clean-stale` on the next `make tools`/`make` run (deleted-tool names are history-derived). `notify-health` stays.
5. Delete the temporary day files and startup log. `notify-health.jsonl` is untouched:

   ```
   rm -f ~/.local/state/notify-debug/*.jsonl
   rmdir ~/.local/state/notify-debug 2>/dev/null || true
   rm -f ~/.local/state/notify-startup.log
   ```

   No archive step is taken (hard delete); the instrumented source remains in git history under the tag.
6. Post-dismantle acceptance checks:
   - `grep -rn 'notify-log-send\|notify_log_send' dotfiles tools etc lib` returns nothing (only the `/usr/bin/notify-send` fallback shim remains, which is the correct end state).
   - `systemctl --user list-units 'notify-*'` shows only `notify-health.service`.
   - `~/.local/state/notify-health.jsonl` keeps growing on the next notification; `~/.local/state/notify-debug/` is absent.
   - `git show --stat diag/notification-logging-final` still resolves (tag survives).

Capabilities lost (by design — re-arming = checkout the tag and re-apply):

- `missing_fire` detection (attempt with no matching `observed_notify` past the 60 s grace window).
- `wrong_time` detection (`observed_at − expected_at` beyond 5 s).
- Suppressed-decision diagnosis (`cooldown`, `no-device`).
- The `occurrence` index and day-boundary stitching.
- Env/PID-chain capture inside `attempt` records.

What survives: `notify-health.service` writing `kind:"health"` metadata-only records (app name, timestamp, fired/blocked, DND-state-at-fire) to `~/.local/state/notify-health.jsonl`, indefinitely.

Tag, branch and retrieval path:

- Anchor: tag `diag/notification-logging-final` (created at finalization close) pins the last fully-instrumented commit. It is the reactivation entry point and outlives the branch.
- Branch `diag/notification-logging` is a working branch, not an archive: after the true merge to `main` it may be deleted with `git branch -d` at normal discretion. Deleting it removes only the name and its reflog; the commits remain reachable from `main` via the merge commit and from the tag.
- Reactivation: `git worktree add /tmp/opencode/notify-reactivate diag/notification-logging-final` (or checkout the tag in a throwaway worktree), then follow §Setup. Never re-apply by hand from memory — the tagged tree is the source of truth.

## Archive / retrieval

- **Wrong-time diagnosis:** thresholds and the manual journalctl cross-read are in the `wrong_time`/`missing_fire` bullet under §5 (Correlation ID and visibility rules).
- **Reactivation path:** checkout tag `diag/notification-logging-final` and re-apply per §Setup. The tag is the durable anchor; the branch is not.
- **Retrieving dismantled code:** `git show diag/notification-logging-final:<path>` (e.g. `git show diag/notification-logging-final:tools/notify-reconcile.py`) or `git worktree add /tmp/opencode/notify-reactivate diag/notification-logging-final`. Never reconstruct from memory — the tagged tree is the source of truth.
- **Retrieving historical day files:** they are not in git (git-ignored state). If a copy was kept before dismantling, it lives wherever it was archived; otherwise only the journal and the permanent `notify-health.jsonl` remain.

## Verified machine versions

notify-send 0.8.6 (`-p`, `-r`, `-t` work), swaync 0.11.0, reference `dbus-daemon`, persistent journal (2026-11-02).
