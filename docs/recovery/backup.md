# backup — manual runbook

This file owns the restic backup/restore pipeline, custody, and the
manual commands. (The gopass store and GPG key-export stream were
removed — see `../postmortem/gopass-removal-postmortem.md`.)

Path note: the notes vault lives at `/home/mike/knowledge_base`
(`config.txt` `OBSIDIAN_VAULT_PATH`). The restore code names the same
directory by its home-relative literal `knowledge_base`
(`lib/40-restore.py` `LIVEDATA_TARGETS`) — deliberately never derived
from the config var, so the var's value is a convenience here, not a
dependency. Both spellings mean `~/knowledge_base`. The vault path is a
backup-owned restore target: note content restores here and nowhere
else. The vault's `.obsidian/` settings are a repo-owned seed deployed
by `make dotfiles` via the var (`dotfiles/obsidian/*`), excluded from
restic per the mirror rule below — never edit them into the backup.

Terminology: `vault` = the Obsidian notes vault. (Historical `store`
meant the gopass password store — gone.)

## pipeline (plain language, no commands)

Backup (livedata): livedata paths pass through the excludes filter
(repo mirrors, caches, downloads, and other noise drop out), then restic
chunks and deduplicates the remainder, compresses it, encrypts it with
AES-256 using a repo password held in Bitwarden (typed at the `/dev/tty`
prompt chain or supplied via flags/env), and uploads the ciphertext to
the B2 EU-Central bucket. Each run appends a new immutable snapshot
indexed by host. There is a single stream; `--tag` is only a generic
restic selector.

Restore: ciphertext downloads from B2, then decrypts with the
lib-owned password chain (a password file flag first, then the
`--password-command` flag or ambient `RESTIC_PASSWORD_COMMAND`, then the
operator-typed Bitwarden-held string over `/dev/tty`), then
decompresses into a staged directory under `/tmp`, shows a preview of
the incoming file list, and — only when every target is absent or empty
— atomically moves paths into place with owner-only modes and user
ownership, never deleting anything.

B2 sees ciphertext only. Server-side encryption is never trusted as the
confidentiality boundary.

## setup

Manual B2 prerequisite (done once): EU Central private bucket with
default encryption enabled and object lock disabled; a dedicated
S3-compatible app key restricted to that bucket. Bucket name and endpoint
are non-secrets in committed `config.txt` (`RESTIC_REPOSITORY`,
`RESTIC_HOST`); the key ID and secret live in Bitwarden and never in the
repo.

The phone needs the Bitwarden app present and synced at bootstrap time;
nothing on the bootstrap path opens a URL. Fix the empty-vault abort,
triage `~/Downloads` into `~/Videos/keep/`, and record bucket plus
password custody before relying on the chain.

Custody (operator-held, before first run — restic never generates the
password):

- restic repo password — Bitwarden item (sole copy).
- S3 keyID + applicationKey — Bitwarden item.

Runtime reads: `lib/helpers/restic_common.py` resolves secrets from
flags/env first, else `/dev/tty` prompts, exporting S3 credentials into
the restic subprocess environment only, never logging secret values.

## excludes

Locked file: `etc/restic/excludes.txt`. One pattern per line, `#`
comments and empty lines ignored, `$HOME` prefix expanded (tilde never
used). Repo mirrors, wholesale `~/.config` and `~/.local/*`, caches,
downloads, toolchains, browser profiles, VMs, disk images, models,
`/opt/ark`, and WiFi secrets are excluded. The `keep/` re-include pair
stays last. A missing excludes file fails the run naming the exact path.

Repo-mirror rule: if `git clone + make all + ./install.py` recreates it
byte-identical, it is excluded. The Obsidian settings directory is a
mirror; the notes content is livedata.

## manifest (`~/Videos/keep/`)

`keep/` is the sole backed-up media path — move a file into it to back
it up. Its audit file is `keep.manifest` with one line per file:
`sha256 path bytes reason`, with a non-empty reason. The manifest file
itself is skipped in the audit.

The checker is `lib/helpers/check_keep.py` (stdlib only; exit 0 pass, 1
rejection, 2 usage). Every on-disk file needs a matching line with equal
hash and size; every line needs a matching file. Totals above 1 GiB warn
but pass; above 3 GiB reject. Empty directory with no manifest passes.
Backup preflight runs the checker; the restore path never does.

## custody

Sole authoritative table (secret → runtime home → recovery home):

- Repo password: `/dev/tty` prompt chain (or flag/env) → Bitwarden
  (sole copy). Bootstrap input on blank iron, not just recovery hygiene.
- B2 keyID + applicationKey: `/dev/tty` prompts (or ambient `AWS_*`) →
  Bitwarden.

Cadence: rotate the Bitwarden entries on their own schedule; the repo
follows via the normal re-init path. Per-tag forget applies to any tag
you pass.

## manual backup

All runs go through the standalone human CLI (never raw restic):

```sh
python3 lib/40-backup.py --preview-only   # history: latest snapshot + +N -M verdict, zero mutation
python3 lib/40-backup.py --dry-run        # future: what the next backup ships (excludes applied), no snapshot
python3 lib/40-backup.py --yes            # livedata backup (fast-forward; prompts only on removals)
python3 lib/40-backup.py --yes --cleanup  # + per-tag forget --prune after backup
backup-preview                            # thin PATH wrapper: --preview-only --yes (via make dev loop)
```

| Flag | Meaning |
| --- | --- |
| `--preview-only` | `snapshots --latest 1` + `diff`, worth-it verdict on removals |
| `--dry-run` | top-level WILL/SKIPPED gate via `backup --dry-run --json`, scans `~`, writes nothing, ignores `--cleanup` |
| `--yes` | non-interactive fast-forward, skips the eyeball gate, no prompts |
| `--cleanup` | per-tag `forget --prune` after backup |
| `--tag` | generic restic snapshot tag selector |
| `--keep-dir` | override `~/Videos/keep` for the keep checker |
| `--skip-if-unchanged` | opt out of fast-forward append |
| `--password-file` / `--password-command` | repo password via file/command instead of the TTY prompt |

Livedata ships `~` filtered by `etc/restic/excludes.txt` (deny-by-default
`$HOME/.*`, re-include `!$HOME/.ssh`). What lands on a
fresh machine is the restore target set (`lib/40-restore.py`
`LIVEDATA_TARGETS`): `knowledge_base`, `Videos/keep`, `.ssh`.

Preflight: binary → excludes stat → keep checker → repo env (flags/env
then `/dev/tty` prompts).
`--dry-run` prints the Tier-B offline overview (non-dot tops listed,
dotfiles collapsed to a count line — grouping by leading dot is
presentation, never filter emulation) plus the Tier-A
`--dry-run --json` aggregation by top-level `~/*`: `WILL` split into
folders + files with restic `summary` counters, `SKIPPED by
excludes.txt` (non-dot majors listed; dotfiles collapsed to a counter —
explicit `excluded_item` plus local tops absent from a
successful scan — with rc=0 absence means excluded; scan-root items
are dropped, never shown), `UNCHANGED`. Majors only, no per-file
flood — and writes nothing. Interactive `backup` (TTY, no
`--yes`) runs the same gate and blocks on `[y/N]` before the real
scan (fail-closed on dry-run error; `N` writes nothing).
Fast-forward default (identical re-runs ~0B); `--yes`/non-TTY skips
the gate; post-snapshot prompts only on removals;
non-TTY always fast-forwards.

Example (`--dry-run`, zero mutation):

```
40-backup: on-disk top-levels (NOT filter truth — excludes not applied):
  ~/Documents  780M  NEW-ON-DISK
  ...
  dotfiles: 33 on disk, 31 hidden by collapse ($HOME/.* deny — see WILL/SKIPPED below)
40-backup: WILL backup — 9 folders + 12 files (100 new + 14 changed files, 9 new + 0 changed dirs, 665.8MiB new):
  folders:
    ~/Music  638.9MiB  3 changed
    ...
  files:
    ~/todo.md  577B  1 changed
    ...
40-backup: SKIPPED by excludes.txt:
  ~/Downloads  780M
  ~/go  2.7G
  dotfiles: 30 skipped ($HOME/.* deny)
```

Forget policy: `--keep-daily 7 --keep-weekly 4 --keep-monthly 6
--group-by host,paths,tags --prune`. The group-by spelling is verified
against restic 0.18.0.

## manual restore

Standalone operation only, after `./install.py` has converged, the
reboot is taken, and the system verified stable.

1. Fail-loud preconditions (restic binary, skeleton target dirs exist
   and are never auto-created, network alive, excludes file clean,
   no pending reboot — `--allow-pending-reboot` overrides with an
   operator-owned warning).
2. Preflight 10 = missing, 11 = locked, 12 = wrong password. Locked
   repos use `--retry-lock 10m` on every invocation; never automatic
   unlock — stale lock means manual `restic unlock` after confirming no
   live holder.
3. Blank-iron gate: every target absent-or-empty or fail-stop with the
   conflict list. Populated targets are refused by design.
4. Best-effort `pre-restore-<ts>` safety snapshot (failure warns and
   continues).
5. Staged restore to `/tmp/restore-<ts>` with verify, preview of the
   incoming file list, atomic per-path move-in parents-first, 600/700
   plus user ownership hardened on staging copies before swap, no
   `--delete` anywhere. Success removes staging; failure keeps it and
   logs the path.

Password chain: `--restore-password-file` → `--password-command`
(or ambient `RESTIC_PASSWORD_COMMAND`) → `/dev/tty` prompt
(Bitwarden-held). Headless without flags/env fails naming them. B2
credentials follow the same shape: ambient `AWS_*` pair, else `/dev/tty`
prompts for keyID and applicationKey (both Bitwarden-held).

Every terminal outcome emits a `RESULT {json}` trailer on stdout
(`status`/`changed`/`message`); `install.py`'s summary prefers it over
the exit code. Standalone exit code 2 is retired — bad flags now FAIL
with rc=1.

```sh
python3 lib/40-restore.py --preview-only        # stage + preview, move nothing in
python3 lib/40-restore.py                       # livedata standalone restore
python3 lib/40-restore.py --snapshot <id>       # non-latest snapshot
python3 lib/40-restore.py --restore-password-file <path>  # headless path
```

| Flag | Meaning |
| --- | --- |
| `--preview-only` | stage to `/tmp/restore-<ts>` + preview, move nothing in |
| `--snapshot` | snapshot ID or `latest` (default) |
| `--tag` | generic restic snapshot tag selector |
| `--restore-password-file` | path to file holding the repo password |
| `--password-command` | override password command (no default reader) |
| `--allow-pending-reboot` | proceed despite reboot latch (operator owns risk) |
| `--overwrite` | `if-changed` only — additive move-in, no `--delete` anywhere |

First-run order: bare `./install.py` → reboot →
verify-stable → `python3 lib/40-restore.py` (livedata standalone).

## triage criteria

- Into `keep/`: media worth the 1–3 GiB budget with a manifest reason
  that justifies permanent off-site storage.
- Out: downloads, installers, re-downloadables, anything the repo
  recreates, anything over budget or undocumented (the checker rejects
  it before it can become an immutable snapshot).

## custody-record format

For each secret: Bitwarden item name and field, date stored, date
verified with a read-back, and rotation due. Verify the Bitwarden-held
restic-password entry exists before relying on the chain.
