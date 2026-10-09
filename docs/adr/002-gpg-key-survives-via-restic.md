# GPG key survives via restic-encrypted export in B2

- Status: Accepted (2026-09-27) · Supersedes: — · Source: Step 0b
  decision 2026-09-27 (option b only, per owner ruling)

## Decision

The GPG secret-subkey + ownertrust export lives restic-encrypted
inside the B2 repo. Recovery chain: human-held restic password
(Bitwarden/paperkey) → B2 repo → key export → `gpg --import` → store
decryptable. The 40 restore path owns key-material import/export;
20 §20.5 store-init depends on it. Complements the Step 0b USB
offline export (40-backup.md), does not replace it: USB is the
offline leg, the B2 copy is the in-repo recovery leg.

## Why

- The store is encrypted to the key, so the key cannot live only
  inside the store — recovery must root outside it.
- Invariant: the restic password stays human-reachable outside the
  store (never in repo, never in restic); that breaks the circularity.
- Revisit only if: the custody model changes (e.g. hardware-backed
  keys remove the export-the-key step entirely).

## Consequences

- Good: one recovery chain, no separate key-escrow service; the
  new-machine flow is "password in, everything else follows".
- Bad (accepted cost): losing the restic password loses everything —
  Bitwarden + paperkey custody is load-bearing, not advisory.

## Amendment (2026-10-09)

Offline USB/paper leg waived on this single-stick system by owner
decision; the B2 vault-stream copy is the sole off-disk key leg.
Verdict unchanged.
