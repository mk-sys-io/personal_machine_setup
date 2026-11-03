# Notification system reassessment — unified integration

status: assessment · no decision locked · created 2026-11-03
review-by: 2027-05-03 · effort: L · urgency: soon

Scope
------
Context document, not an implementation plan. Distills the 2026-11-03
debugging session into a state-of-the-system assessment with observations,
findings, suggestions, caveats, and unverified claims — sufficient for a
future agent or human to pick the stream up. Companion doc (temporary debug
system + dismantling runbook): `docs/notification-debugging.md`. Task entry:
019 in `docs/backlog/backlog.md`.

Origin
------
For a single fix, the 2026-11-03 debug session surfaced two independent
notification faults operating against the same background:
(1) `bluetooth-idle` "no radio present" visible popup every ~60 s while sway
was up (should have been log-only). (2) the wlsunset 17:00 blue-light
transition notification never fired.

Recurring insight: every failure logged was in the notification layer only —
the underlying action (power off adapter, shift gamma, poll battery) always
succeeded. Hypothesis: producers integrate with the notification stack in
arbitrary ways; integration, not the transport, is the failure surface.

Bug receipts (from the debug system's own day files + subagent audits)
----------------------------------------------------------------------
| Bug | Root cause | Status |
|---|---|---|
| "no radio present" visible popup every 60 s | systemd user-manager PATH lacks `~/.local/bin`; `command -v` misses helper; fallback shim ignores `NLOG_SUPPRESS` | fixed 2026-11-03 (`bluetooth-idle-monitor.sh`, `notify-user.sh`, absolute path — uncommitted) |
| 17:00 wlsunset notification never fired 2026-11-03 | `wlsunset-notify.py` sleeps via one `time.sleep()` (CLOCK_MONOTONIC, frozen during ~9 h suspend); wake would land ~9 h late; sway reload 17:39 killed daemon first | open (design) |
| Morning 06:00 transition missed | boot after boundary; no catch-up at startup | open |
| BT radio instant power-off after resume | timer cadence is monotonic, idle state is wall-clock epoch file | open |
| Suppressed-but-visible popups (battery/sys-alert) | fallback shim path — same class as the bluetooth bug | open (fixed only in 2 producers) |
| sys-alert cooldown counts suspend time | wall-clock cooldown vs monotonic suspend | open |
| swaync rule/wire mismatches | `system-monitors-boot` rule needs transient but send has no `-e`; battery-alert has no app-name; clipboard sends `-a ""` (unmatchable) | open |
| Reconciler noise | suppressed attempts flagged as `missing_fire`; one observed fire produced 26 `unknown_fire` records (no dedup) | open |
| 2026-11-02 17:00 fired at 16:00:00.186Z (exact) but with no `attempt` record | sway-session PATH also lacks `~/.local/bin`; `shutil.which` fell back to raw notify-send; only the D-Bus observer saw it | open (same PATH class) |

Integration-pattern audit (2026-11-03)
--------------------------------------
- Helper resolution: three idioms coexist — `command -v notify-log-send` shim ×7,
  absolute-path `[ -x ]` ×2, `shutil.which... or /usr/bin/notify-send` ×2.
- "Don't show" implemented four ways: env `NLOG_SUPPRESS`, `trigger.decision`
  JSON, exit-0 guard, absent `-e` flag. The fallback shim nullifies all of them.
- Two clock models mixed: monotonic (systemd timers, Python sleep) vs
  wall-clock epoch files (bt-idle state, sys-alert cooldowns, `expected_at`).
  Suspend drives a wedge between them.
- Hardcoded environment: `unix:path=/run/user/1000/bus` (`battery-alert.sh:10`),
  uid 1000 (`notify-user.sh:22`), guessed `wayland-1` (`autostart.sh:93`).
- Swaync visibility covers 4 of 11 producers; visibility state and renderer
  flags disagree in at least two places.
- Assessment: transport/daemon is sound (swaync maintained, upstream push
  2026-06-25). The sand is entirely producer-side. Every bug above is an
  integration bug, not an action-path bug.

Ecosystem survey (verified: fetched man pages / wiki / READMEs / GitHub API,
2026-11-03; websearch unavailable — HTTP 402 — links verified by direct fetch)
----------------------------------------------------------------------------
Already solved upstream (candidates: adopt):
- Volume/brightness overlays — `wob` (github.com/francma/wob) or
  `SwayOSD` (github.com/ErikReider/SwayOSD). Kills two producers plus the
  `x-canonical-private-synchronous` hint machinery and its rule.
- Battery alerts — `batsignal` (github.com/electrickite/batsignal; ships a
  systemd user service) or `poweralertd` (sr.ht/~kennylevinsen/poweralertd).
- Fixed-clock scheduled transitions — `gammastep` hooks (documented
  `period-changed` notify hook, `dawn-time`/`dusk-time` HH:MM) OR keep
  `wlsunset` + systemd user timer `OnCalendar=*-*-* 06:00,17:00` with
  `Persistent=true`. Systemd documents that OnCalendar timers catch up after
  suspend ("once the system is later resumed it will catch up and process all
  timers that triggered while the system was sleeping") and Persistent covers
  missed runs while powered down. Either route replaces the fragile
  `time.sleep()` scheduling model.
- Extended notify-send features — `dunstify` (maintained, dunst 1.13.2) or
  `notify-send.py`; `notify-send.sh` feature-frozen since 2022-03-03.

Genuinely niche (no maintained OSS found; keep custom):
- BT-idle radio power-off, clipboard "Copied" toast, shutdown-abort notify,
  per-source cooldown. (Searched ArchWiki, sway wiki, GitHub API; negative
  evidence, not exhaustive.)
- Persistent notification logging/observability — no OSS provides it for
  swaync/mako/dunst at all (verified by the same absence sweep). The
  build-your-own-logging decision was sound; only the placement was wrong.

Daemon-side choke point that already exists
--------------------------------------------
swaync `scripts` config hook with no matchers and `run-on: "receive"` runs for
EVERY incoming notification regardless of suppression/DND/inhibition, with
env vars `$SWAYNC_APP_NAME`, `$SWAYNC_SUMMARY`, `$SWAYNC_BODY`,
`$SWAYNC_URGENCY`, `$SWAYNC_ID`, `$SWAYNC_HINT_*`. A daemon-side logger there
- replaces producer-side attempt logging (logging no longer depends on
  producers resolving any helper — kills the silent-fallback class),
- sees per-notification detail including the correlation hint directly.

Limitations (verified in `swaync.5.scd` + source): only the first matching
script runs per bucket; only sees traffic that reaches swaync (no
daemon-owner events, no close signals — the D-Bus `BecomeMonitor` observer
covers those); no `NotificationClosed` on popup expiry (documented in
`docs/notification-debugging.md`).

Assessment
----------
- A full custom broker (own send API, intercepting all notifications) is NOT
  recommended: re-implements a maintained spec surface, no upstream fixes
  inherited, high maintenance for no unique gain.
- A thin custom producer-side interface IS justified: one `tools/notify`
  wrapper (absolute-path helper resolution + fallback, wall-clock
  `expected_at`, one suppression spelling, app-name defaulting, documented env
  contract) replaces the 7 shim copies, 8 trigger-JSON blocks, and the
  hardcoded uid/bus lines. Small, repo-owned, no upstream to break.
- Adopt upstream tooling where it exists so whole bug classes die instead of
  being patched: wob/SwayOSD, batsignal, gammastep-or-timer+ palette bridge.
- Keep the four niche producers, moved onto the thin wrapper.
- Logging/debugging: swaync catch-all scripts hook (daemon-side) + the
  permanent `notify-health` observer as the long-term observability stack;
  the temporary attempt-side machinery then dissolves per the dismantling
  runbook in `docs/notification-debugging.md`.

Suggested final shape (order-of-work sketch, not a plan)
--------------------------------------------------------
1. swaync catch-all logger script + cutover of the temporary components.
2. Thin `tools/notify` wrapper; migrate producers one by one.
3. Adopt wob (or SwayOSD), batsignal, and the gammastep/timer route.
4. Reconciler hygiene: skip `decision=suppressed` attempts in `missing_fire`;
   dedup `unknown_fire` per (observed_rt, summary).
5. Execute the `notification-debugging.md` dismantling once 1) is verified.

Open decisions (must be taken before work starts)
---------------------------------------------------
- PATH strategy: per-producer absolute paths (validated 2026-11-03) vs
  repairing the login-shell PATH itself (sway is spawned by `login`; the
  session PATH lost `~/.local/bin`, `.kilo/bin` is suspected of rewriting
  it). Option B heals everything at once but has a larger blast radius.
- gammastep hooks vs systemd timer for the palette bridge: gammastep's
  resume-after-suspend behavior is UNVERIFIED, needs a one-shot test before
  adoption.
- Whether to keep the D-Bus `BecomeMonitor` observer alongside the swaync
  hook (recommended) or drop it entirely.

Caveats and unverified claims
------------------------------
- Websearch was down during research (HTTP 402); all ecosystem claims come
  from fetched ArchWiki/sway wiki/man pages/READMEs and GitHub API searches —
  negative-evidence conclusions are strong but not airtight.
- Activity claims pinned at fetch time 2026-11-03: wob pushed 2026-09-05,
  SwayOSD 2026-06-22, gammastep 2026-09-02, notify-send.sh frozen 2022-03-03,
  poweralertd stale-ish (~2024), batsignal AUR git pkg orphan since 2024-09.
- swaync `scripts` mechanics were read from current main source + man page;
  installed daemon is 0.11.0 — smoke-test the catch-all hook on the installed
  version before relying on it.
- `dunstify -w/--wait` close-reason semantics depend on `NotificationClosed`,
  which swaync does not emit on expiry — do not rely on `-w`.
- systemd `WakeSystem` is not generally available for user units; do not
  design around waking the machine.
- The 2026-11-03 producer fixes (bluetooth + notify-user absolute path, doc
  note) remain uncommitted; the work documented here is repo-only at time of
  writing.
- The two GPS-class bugs (bt-idle timer/realtime mismatch, sys-alert cooldown
  suspend inflation) have NOT been reproduced by test; both were found by
  model comparison, not observed in the wild.
