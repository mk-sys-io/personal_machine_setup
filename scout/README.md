# Scout

Self-hosted facade over every free-tier LLM provider: one OpenAI-compatible
endpoint at `localhost:4000/v1`, aggregated catalog refreshed daily, filtered to
text-only models, editable blacklist. See docs/adr/016-litellm-substrate-scout-awareness.md,
docs/adr/017-detection-is-declarative-descriptors.md, providers/README.md, and docs/operating.md.

## Architecture

```
scout/providers/*.yaml  --make scout-->  live copy (D17)
        |                                    |
        v                                    v
Scout (~200 lines Python, stateless) ---> LiteLLM (localhost:4000/v1)
  1. fetch provider catalogs                 STORE_MODEL_IN_DB=true
  2. diff vs cached snapshot
  3. blacklist -> free predicate -> keyword filter
  4. POST /model/new + DELETE /model
  5. write snapshot + daily report
Triggers: scout.timer (24h) | scout-reloader.path | make scout | systemctl
```

## Layout

| Path | Purpose |
|---|---|
| `providers/README.md` | Descriptor schema + matcher grammar (doc-first, D5) |
| `providers/*.yaml` | One descriptor per provider (control plane, D16) |
| `blacklist.yaml` | Two-tier blacklist (D7) |
| `scout.py` | Stateless runner + CLI (`validate`, `diff`, `run`) |
| `docs/operating.md` | Runbook (D19 #3) |

## Commands

- `make scout` — sync repo truth to live paths (D17).
- `scout validate [file...]` — contract check, no fetch, no writes.
- `scout diff [--provider X]` — dry-run fetch + predicate chain.

## Docs

- `providers/README.md` — schema, grammar, admission checklist (D13), confidence taxonomy (D14).
- `docs/operating.md` — runbook (D19 #3).
