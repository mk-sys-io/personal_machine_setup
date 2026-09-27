# gopass is the sole API-key store; registry splits from Pi

- Status: Accepted (2026-09-28) · Source:
  `plans/completed/gopass-api-key-management.md`

## Decision

gopass is the single, no-fallback API-key store: one structured entry
per provider at `provider-registry/<id>` (`key` plus pi_setup's
`strategy` field). `vault.json` is removed entirely; a machine without
gopass cannot use the registry until install runs. Alongside,
`provider_registry` splits into a shared module (identity, endpoints,
chat contract) with all Pi-specific concerns pushed into `pi_setup`.

## Why

Fully offline under Ark lockdown in all modes — no allowlist changes,
no accounts, no cloud. `gopass env` injects secrets into process env
so values never land in a context window or on disk; Pattern 1/2/3
consumption follows module lifecycle (`docs/gopass.md` holds the
mechanics — this record holds the verdict).
`Rejected: bw CLI — offline-broken, master password, Node startup;
bws — cloud-dependent, paid tier, needs allowlist exception; KeePassXC
— GUI-oriented, not headless; vault.json plus env fallback — removed,
single-machine use; provider_registry cli.py plus __main__.py — use
the gopass CLI directly.`
`Revisit only if: none named — adoption is unconditional.`

## Consequences

- Good: one store, one workflow (`gopass insert`), agent-safe by
  construction; exit-code-mapped errors (FM1–FM7) give precise
  remediation.
- Bad (accepted cost): gopass plus GPG plus an initialized store are
  hard prerequisites; first use surfaces interactive pinentry.
