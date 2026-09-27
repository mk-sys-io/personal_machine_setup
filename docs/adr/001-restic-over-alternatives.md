# Stay on restic; S3 API, accept stale-lock ops cost

- Status: Accepted (2026-09-27) · Supersedes: — · Source: tool trial
  2026-09-27 (borg/kopia/duplicacy/rclone/bupstash)

## Decision

Restic remains the backup tool (reaffirms D1, D18; absorbs D20). Talk
to B2 via the S3-Compatible API only (`s3:`, never `b2:`). All
automated invocations pass `--retry-lock 10m`; stale locks clear by
manual `restic unlock`, never automatic `unlock --remove-all`.

## Why

- Restic is the only apt-native + S3-direct + JSON/exit-code-scriptable
  candidate; everything else fails at least one hard constraint.
- Rejected: borg — no native S3; kopia — non-apt + coarse exit codes
  (named fallback, switching cost recorded); duplicacy —
  closed-source; rclone — sync, not backup; bupstash — no B2/retention
  story.
- Rejected: restic's native `b2:` backend — known error-handling
  defects; upstream recommends the S3-compatible API (needs an S3 app
  key; the master key isn't S3-compatible).
- Revisit only if: kopia lands in Debian stable with structured
  machine output, or restic's `b2:` backend is fixed upstream.

## Consequences

- Good: zero new deploy channels (apt only); scriptable preflight and
  lock-retry; one tool for backup/restore/check/copy.
- Bad (accepted cost): B2 stale locks are a documented failure mode
  (restic#2562) — operators own the manual-unlock runbook procedure.
