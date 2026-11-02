# Scout

Self-hosted facade over every free-tier LLM provider: one OpenAI-compatible
endpoint at `localhost:4000/v1`, aggregated catalog refreshed daily, filtered to
text-only models, editable blacklist. Free tiers only — no paid API keys are ever
added to the registry. LiteLLM is the gateway; the scout is the awareness layer
that feeds it and never holds raw upstream keys. See docs/adr/016-litellm-substrate-scout-awareness.md,
docs/adr/017-detection-is-declarative-descriptors.md, providers/README.md, and docs/operating.md.

Deploy: manual — `make scout` (not part of `make all` or install.py); see docs/operating.md §7 and backlog 018.

## Architecture

```
scout/providers/*.yaml  --make scout-->  live copy (repo-truth sync; see Makefile)
        |                                    |
        v                                    v
Scout (~200 lines Python, stateless) ---> LiteLLM (localhost:4000/v1)
  1. fetch provider catalogs                 STORE_MODEL_IN_DB=true (Postgres via user quadlet)
  2. diff vs cached snapshot
  3. blacklist -> free predicate -> keyword filter
  4. POST /model/new + DELETE /model
  5. write snapshot + daily report
Triggers: scout.timer (24h) | scout-reloader.path | make scout | systemctl
```

## Layout

| Path | Purpose |
|---|---|
| `providers/README.md` | Descriptor schema + matcher grammar (doc-first) |
| `providers/*.yaml` | One descriptor per provider (control plane — file edits are the interface) |
| `blacklist.yaml` | Two-tier blacklist (global + per-provider) |
| `scout.py` | Stateless runner + CLI (`validate`, `diff`, `run`) |
| `docs/operating.md` | Runbook |
| (scope) | v1 scope: Kilo Gateway + OpenRouter only (NaraRouter dropped — plan-allowlist would need a matcher not built under YAGNI). |

## Commands

- `make scout` — sync repo truth to live paths.
- `scout validate [file...]` — contract check, no fetch, no writes.
- `scout diff [--provider X]` — dry-run fetch + predicate chain.

## Docs

- `providers/README.md` — schema, grammar, admission checklist, confidence taxonomy.
- `docs/operating.md` — runbook.
