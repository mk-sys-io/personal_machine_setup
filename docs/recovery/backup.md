# backup — manual runbook

Complements `../gopass.md`. That file owns gopass patterns and failure
modes; this file owns the restic backup/restore pipeline, custody, and
the manual commands.

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

Terminology: `store` = the gopass password store
(`~/.local/share/gopass`); `vault` = the Obsidian notes vault. The two
words never interchange.

## pipeline (plain language, no commands)

Backup (livedata): livedata paths pass through the excludes filter
(repo mirrors, caches, downloads, and other noise drop out), then restic
chunks and deduplicates the remainder, compresses it, encrypts it with
AES-256 using the password held in `services/restic` via gopass, and
uploads the ciphertext to the B2 EU-Central bucket. Each run appends a
new immutable snapshot indexed by host.

Backup (vault stream): the store directory plus the GPG key-export
directory travel as explicit paths with no excludes file, through the
same chunk, compress, encrypt, and upload steps, and land as a `--tag
vault` snapshot in the same repo. Snapshots stay KB-scale by design.

Restore: ciphertext downloads from B2, then decrypts with the
lib-owned password chain (a password file flag first, then the gopass
password command, then the operator-typed Bitwarden-held string over
the terminal since gopass itself arrives via this restore; three wrong
attempts fail naming the Bitwarden vault), then decompresses into a staged
directory under `/tmp`, shows a preview of the incoming file list, and
 — only when every target is absent or empty — atomically moves paths
into place with owner-only modes and user ownership, never deleting
anything.

Blank-iron order is GPG key import first, then the latest `--tag vault`
restore, then agent convergence, then a decrypt check proving the store
works, then everything else. The store clone from its git remote happens
after the restore, never as the bootstrap path.

B2 sees ciphertext only. Server-side encryption is never trusted as the
confidentiality boundary.

## setup

Manual B2 prerequisite (done once): EU Central private bucket with
default encryption enabled and object lock disabled; a dedicated
S3-compatible app key restricted to that bucket. Bucket name and endpoint
are non-secrets in committed `config.txt` (`RESTIC_REPOSITORY`,
`RESTIC_HOST`); the key ID and secret live in gopass and never in the
repo.

GPG and gopass init come first. The
phone needs the Bitwarden app present and synced at bootstrap time;
nothing on the bootstrap path opens a URL. Fix the empty-vault abort,
triage `~/Downloads` into `~/Videos/keep/`, and record bucket plus
password custody before relying on the chain.

GPG private-key backup (one-time plus on rotation): stage
`secret-subkeys.asc` + `ownertrust.txt` in `~/.local/share/gpg-export/`
(700 dir, 600 files) so the pair rides the vault stream (on-disk path
outside the store directory so it never syncs to GitHub). The export
stays passphrase-locked; the passphrase lives in the Bitwarden vault,
never in this repo and never in restic. On this single-stick system
there is no USB export and no paper print —
the vault-stream B2 copy is the sole off-disk key leg; the revocation
certificate stays in `~/.gnupg/openpgp-revocs.d/` only. The `~/0x*.pub.key`
public file is not backed up (regenerable, duplicated in the store).

Required gopass entries (operator-invented repo password, stored before
init — restic never generates it):

- `services/restic` field `key` — the repo password.
- `services/b2` fields `keyID` + `applicationKey` — the S3 credentials.

Missing entries fail loud with the `gopass insert` hint. The canonical
reader is `lib/helpers/gopass.sh` (executable CLI, never sourced); the
runtime exports S3 credentials into the restic subprocess environment
only (`lib/helpers/restic_common.py`), never logging secret values.

Agent posture: `~/.gnupg/gpg-agent.conf` (live-home only, never
committed) carries monthly cache TTLs, terminal-only pinentry, and
preset support; `GPG_TTY` is exported from `dotfiles/bashrc` and set
in-process in the restore gate. First decrypt prompts in-terminal, warm
re-reads stay silent.

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

- Repo password: gopass `services/restic` → Bitwarden (sole copy).
  Bootstrap input on blank iron, not just recovery hygiene — a
  store-only password makes restore circular.
- B2 keyID + applicationKey: gopass `services/b2` → Bitwarden.
- GPG passphrase: human memory / Bitwarden only — never gopass (that
  would be circular).
- GPG export files (`secret-subkeys.asc` + `ownertrust.txt`): staging dir
  `~/.local/share/gpg-export/` → B2 `--tag vault` snapshot (ciphertext).
  Sole off-disk key leg on this single-stick system.

Cadence: vault backup after every insert or rotation; dedicated timer
later. Per-tag forget keeps the two streams on independent retention in
the shared repo.

## manual backup

All runs go through the standalone human CLI (never raw restic):

```sh
python3 lib/40-backup.py --preview-only   # history: latest snapshot + +N -M verdict, zero mutation
python3 lib/40-backup.py --dry-run        # future: what the next backup ships (excludes applied), no snapshot
python3 lib/40-backup.py --yes            # livedata backup (fast-forward; prompts only on removals)
python3 lib/40-backup.py --yes --cleanup  # + per-tag forget --prune after backup
python3 lib/40-backup.py --tag vault --yes [--cleanup]  # vault stream, same shape
backup-preview [--tag vault]              # thin PATH wrapper: --preview-only --yes (via make dev loop)
```

| Flag | Meaning |
| --- | --- |
| `--preview-only` | `snapshots --latest 1` + `diff`, worth-it verdict on removals |
| `--dry-run` | top-level WILL/SKIPPED gate via `backup --dry-run --json`, scans `~`, writes nothing, ignores `--cleanup` |
| `--yes` | non-interactive fast-forward, skips the eyeball gate, no prompts |
| `--cleanup` | per-tag `forget --prune` after backup |
| `--tag vault` | vault stream (explicit allowlist paths, same repo) |
| `--keep-dir` | override `~/Videos/keep` for the keep checker |
| `--skip-if-unchanged` | opt out of fast-forward append |

Livedata ships `~` filtered by `etc/restic/excludes.txt` (deny-by-default
`$HOME/.*`, re-includes `!$HOME/.ssh` + `!$HOME/.gnupg`). What lands on a
fresh machine is the restore target set (`lib/40-restore.py`
`LIVEDATA_TARGETS`): `knowledge_base`, `Videos/keep`, `.ssh`, `.gnupg`.
Vault ships `.local/share/gopass` + `.local/share/gpg-export` only.

Preflight: binary → excludes stat → keep checker (livedata only) →
vault allowlist (vault only, before any secret touch) → repo env.
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

Per-tag forget policy (both streams):
`--keep-daily 7 --keep-weekly 4 --keep-monthly 6 --group-by
host,paths,tags --prune`. The group-by spelling is verified against
restic 0.18.0.

## manual restore

Standalone operation only, after `./install.py` has converged, the
reboot is taken, and the system verified stable:

1. Fail-loud preconditions (restic binary, skeleton target dirs exist
   and are never auto-created, network alive, excludes file clean,
   no pending reboot).
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
6. Ordered vault consumer path: GPG key export import first, then the
   store subtree (never wholesale `~/.local/share`), then decrypt
   verify, then convergence.

Password chain: `--restore-password-file` → `--password-command`
(default gopass reader) → TTY prompt (≤3 via `/dev/tty`).
Headless without the file flag fails naming it.

```sh
python3 lib/40-restore.py --preview-only        # stage + preview, move nothing in
python3 lib/40-restore.py                       # livedata standalone restore
python3 lib/40-restore.py --tag vault           # store stream (ordered: GPG import first)
python3 lib/40-restore.py --snapshot <id>       # non-latest snapshot
python3 lib/40-restore.py --restore-password-file <path>  # headless path
```

| Flag | Meaning |
| --- | --- |
| `--preview-only` | stage to `/tmp/restore-<ts>` + preview, move nothing in |
| `--snapshot` | snapshot ID or `latest` (default) |
| `--tag` | snapshot tag selector (`vault` for the store stream) |
| `--restore-password-file` | path to file holding the repo password |
| `--password-command` | override password command (default: gopass reader) |
| `--allow-pending-reboot` | proceed despite reboot latch (operator owns risk) |
| `--overwrite` | `if-changed` only — additive move-in, no `--delete` anywhere |

First-run order: bare `./install.py` → reboot →
verify-stable → automatic vault restore inside `install.py` →
`python3 lib/40-restore.py` (livedata standalone).

## triage criteria

- Into `keep/`: media worth the 1–3 GiB budget with a manifest reason
  that justifies permanent off-site storage.
- Out: downloads, installers, re-downloadables, anything the repo
  recreates, anything over budget or undocumented (the checker rejects
  it before it can become an immutable snapshot).

## custody-record format

For each secret: entry name and field, date stored, date verified with a
read-back, recovery location (Bitwarden item), and rotation
due. Verify the Bitwarden-held restic-password entry exists before relying on
the chain.
