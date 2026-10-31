# LiteLLM is the substrate; the scout is the awareness layer

- Status: Accepted (2026-10-31) · Source: plans/active/free-model-scout/context.md (D11, D12, D17)

## Decision

LiteLLM runs as the inference substrate (rootless podman Postgres + a user unit,
`STORE_MODEL_IN_DB=true`) and owns all upstream provider keys and client-facing
keys. The scout never holds raw upstream keys; it is a stateless awareness layer that
fetches provider catalogs, detects free models, and mutates LiteLLM's registry via
`POST /model/new` / `POST /model/delete`. The scout's only credential is the master key.

## Why

- Triggering constraint: one OpenAI-compatible endpoint at `localhost:4000/v1` must
  aggregate every free-tier provider, with model state persisted across restarts.
- Rejected: compose quickstart — compose lifecycle ≠ systemd user unit, breaks D18's
  sudo-free chain. Rejected: `pip3 --break-system-packages` — undocumented and
  silent-downgrade hazard. Rejected: system units / system Postgres — needs sudo,
  violates D18. Rejected: SQLite/no DB — LiteLLM exits hard with `DATABASE_URL` set
  and no prisma CLI; prisma/psycopg live only under `extra_proxy`.
- Revisit only if: a second inference substrate is admitted, or LiteLLM drops its
  DB-backed registry.

## Consequences

Good: keys live in one place; scout stays stateless and key-free; registry survives
restarts; sudo-free end to end (D18). Bad (accepted cost): Postgres named volume
(`scout-pgdb`) is now part of custody — password must be restored, not regenerated,
if `pg.env` is lost (D20 guard). Two units to keep alive instead of one.