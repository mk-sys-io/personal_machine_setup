# Security

Host intrusion detection and audit logging, deployed by `lib/55-security.sh`.

## Components

- **auditd** — kernel-level syscall and file-access logging. Watches identity
  files, sudoers, SSH config, cron, kernel module loads, input device access,
  and privileged exec. Logs to `/var/log/audit/audit.log`. Query with
  `ausearch -k <key>` and `aureport`.

- **fail2ban** — brute-force login detection. Default Debian config, no custom
  jails. Safety net only; Ark handles network-level blocking.

- **rkhunter** — rootkit signature scanner. **Requires root** — run scans with
  `sudo rkhunter --check --sk` (needs root to read `/var/log`, scan system
  files, and update the signature DB). Baseline built on first install with
  `--propupd`. A **weekly** scan runs via our wrapper
  (`/etc/cron.weekly/rkhunter-scan`, driven by anacron), which sends a
  **clickable** desktop notification on completion — click it to open the log
  in kitty. The wrapper also notifies on scan **failure** (it never swallows
  errors). Notifications are sent by the `notify-user` helper
  (`/usr/local/bin/notify-user`, from `etc/security/rkhunter/notify-user.sh`)
  running in the user's systemd session via
  `systemd-run --user --machine=mike@.host` — the DBus session bus rejects
  root connections, so the notification must run as the user (uid 1000); this
  is independent of sudo group membership. If a notification cannot be
  delivered, the failure is logged to `~/.cache/notify-user.log` (user-writable,
  no sudo needed). Clicking the notification opens the log via **swaync's
  `scripts` mechanism** (`run-on: "action"`, see `dotfiles/sway/swaync/config.json`),
  which runs the click command in the user's graphical session. Because that
  click runs as the user (not root), the wrapper `chmod 644`s the log after
  each scan so it is readable — rkhunter's default is 0644, but the restrictive
  cron umask creates it 0600 root. The Debian package's daily scan is disabled
  (`CRON_DAILY_RUN=false`); its weekly DB update stays enabled
  (`CRON_DB_UPDATE=true`).

- **unhide** — hidden process/port detection. **Requires root** to inspect
  `/proc` and raw sockets. Manual use only:
  `sudo unhide proc` / `sudo unhide-tcp tcp`.

## Config layout

    etc/security/
    ├── auditd/
    │   ├── rules.d/
    │   │   └── 10-security.rules
    │   └── auditd.conf
    └── rkhunter/
        ├── rkhunter.conf.local
        ├── default-rkhunter
        ├── rkhunter-scan.sh        # weekly scan wrapper → /etc/cron.weekly/rkhunter-scan
        └── notify-user.sh          # user-session notification helper → /usr/local/bin/notify-user

## Limitations

- **No real-time alerting.** auditd logs passively. Events are only visible
  when you query the log with `ausearch` or `aureport`. No notifications,
  no popups, no proactive warnings.
- **No anomaly detection.** Rules trigger on all changes — your own edits
  generate the same log entries as a rootkit's. Context (UID, command,
  timestamp) is in the log for you to judge.
- **Ark unrestricted mode is unprotected.** When Ark drops to unrestricted,
  the network allowlist is off and these tools provide zero network-layer
  defense. auditd continues logging locally but cannot detect or block
  outbound exfiltration.
- **rkhunter is snapshot-based.** Only catches known rootkits at scan time.
  New/unknown rootkits between scans go undetected.
- **Weekly scan does not fire on battery.** The weekly cron runs via anacron,
  which by default skips jobs while the system is on battery (to save power).
  If the laptop is closed or on battery at the scheduled time, the scan runs
  on the next boot/AC-power anacron activation instead.
- **Requires `anacron`.** The weekly catch-up behavior depends on the anacron
  package (added to `packages/apt.txt`); it is not assumed present on newer
  Debian installs.
- **Cron runs as root, independent of Ark.** The weekly scan runs as root via
  the cron/anacron daemon, so it is unaffected by Ark removing the sudo group
  or randomizing the root password. No sudoers entry is needed.

## Future enhancements

- Audisp syslog plugin or polling script for real-time notifications on
  high-risk keys (`identity`, `modules`, `input-devices`, `root-exec`).
- AIDE for cryptographic file-integrity baselining (complements rkhunter's
  signature-based approach).
- Custom fail2ban jails for local services if attack surface grows.
- Auditd immutable mode (`-e 2`) after ruleset is validated and rehearsed.
