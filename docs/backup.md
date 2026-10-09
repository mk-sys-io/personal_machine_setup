# backup — manual runbook (40-E)

Complements `docs/gopass.md`. That file owns gopass patterns and failure
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
attempts point at the paperkey copy), then decompresses into a staged
directory under `/tmp`, shows a preview of the incoming file list, and
— only when every target is absent or empty — atomically moves paths
into place with owner-only modes and user ownership, never deleting
anything. Afterwards the system converges and the `curated/` directory
regenerates from its upstream source instead of being restored.

Blank-iron order is GPG key import first, then the latest `--tag vault`
restore, then agent convergence, then a decrypt check proving the store
works, then everything else. The store clone from its git remote happens
after the restore, never as the bootstrap path.

B2 sees ciphertext only. Server-side encryption is never trusted as the
confidentiality boundary.

## setup

Manual B2 prerequisite (40-0, done once): EU Central private bucket with
default encryption enabled and object lock disabled; a dedicated
S3-compatible app key restricted to that bucket. Bucket name and endpoint
are non-secrets in committed `config.txt` (`RESTIC_REPOSITORY`,
`RESTIC_HOST`); the key ID and secret live in gopass and never in the
repo.

GPG and gopass init plus the paperkey copy come first (Step 0). The
phone needs the Bitwarden app present and synced at bootstrap time;
nothing on the bootstrap path opens a URL. Fix the empty-vault abort,
triage `~/Downloads` into `~/Videos/keep/`, and record bucket plus
password custody before relying on the chain.

GPG private-key backup (Step 0b, one-time plus on rotation): stage
`secret-subkeys.asc` + `ownertrust.txt` in `~/.local/share/gpg-export/`
(700 dir, 600 files) so the pair rides the vault stream (on-disk path
outside the store directory so it never syncs to GitHub). The export
stays passphrase-locked; the passphrase lives in the Bitwarden vault,
never in this repo and never in restic. On this single-stick system
(2026-10-09 owner decision) there is no USB export and no paper print —
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

- Repo password: gopass `services/restic` → Bitwarden plus paperkey.
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

All runs go through the standalone human CLI (never raw restic in this
runbook):

- Preview (zero mutation): snapshots plus diff plus worth-it verdict.
- Dry run: no snapshot written.
- Livedata backup, then the same with cleanup (appends per-tag forget
  with prune).
- Vault backup with the vault tag, then the same with cleanup (per-tag
  forget scoped to the vault tag).

Preflight order in the CLI: restic binary present, excludes file stats
clean, keep checker (livedata only), vault allowlist paths exist (vault
only, before any secret touch), then repo environment. Fast-forward by
default — every backup appends a new immutable snapshot sharing blobs;
identical re-runs cost ~0B. Deletion review prompts only on removals in
the human CLI; timers and non-TTY always fast-forward.

Per-tag forget policy (both streams):
`--keep-daily 7 --keep-weekly 4 --keep-monthly 6 --group-by
host,paths,tags --prune`. The group-by spelling is verified against
restic 0.18.0.

## manual restore

Standalone operation only, after `./install.py` has converged, the
reboot is taken, and the system verified stable (stderr banner,
non-blocking; the restore converges nothing itself):

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
   verify, then convergence. `curated/` is never restored — it
   regenerates afterwards.

Password chain is lib-owned: password file flag, then the gopass
password command (covers re-restore on converged machines), then
terminal prompt up to three attempts via `/dev/tty`, then the paperkey
hint. Headless without the file flag fails naming it.

## triage criteria

- Into `keep/`: media worth the 1–3 GiB budget with a manifest reason
  that justifies permanent off-site storage.
- Out: downloads, installers, re-downloadables, anything the repo
  recreates, anything over budget or undocumented (the checker rejects
  it before it can become an immutable snapshot).

## custody-record format

For each secret: entry name and field, date stored, date verified with a
read-back, recovery location (Bitwarden item / paperkey), and rotation
due. Verify the human-held restic-password copy exists before relying on
the chain.

## parked pointers

Timer and cron automation, the 80/90% quota guard implementation, and the
USB second-repo leg are parked in `future/scheduled-backup.md` and
`future/usb-restore.md`. This runbook covers manual operation only.

## proof (unlocks 12-D)

Recorded 2026-10-09 from the 40-E E2 drill (fixture repo under
`/tmp/opencode`, `RESTIC_PASSWORD=drill-only`, no B2 contact; live
zero-mutation reads green the same day: dry-run, preview-only both
streams, `snapshots`, `stats`, `check`, orphan prune of 179 packs /
2.835 GiB with clean `check` after). Live first-backup runs as post-40
manual operation per 40-E E3.

- `40-E proof (notes-vault): 2026-10-09 fixture drill — dry-run showed
  2 files (keep-file + notes, must-excludes present count 0); restore of
  livedata snapshot landed only knowledge_base note + Videos/keep file
  with 0 leaks (no cask, .cache, Downloads, .config, movie.mp4,
  curated/, no store paths); modes enforced dirs 700 / files 600 by
  lib harden (40-D2 V4 fixture-verified).`
- `40-E proof (store-from-B2): 2026-10-09 fixture drill — restore of
  --tag vault snapshot landed only .local/share/gopass/ +
  .local/share/gpg-export/ (secret-subkeys.asc + ownertrust.txt) with 0
  sibling leaks (sibling cask credentials absent); live gopass
  decrypt-verify rides the post-40 first-backup (secret not logged).
  Distinct from the notes-vault proof.`
