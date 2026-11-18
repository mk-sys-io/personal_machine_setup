# Scout — operating runbook

Layered docs: **router** = `AGENTS.md` row + session-start checks; **guide**
= this file; **reference** = `providers/README.md` (schema, matcher grammar,
admission checklist, confidence taxonomy). Published-facing docs wait until the
published-facing rename moment; the working title 'scout' stays for all v1.

## 0. Quick start — first deploy

End-to-end from a fresh machine to a populated LiteLLM registry. Steps marked
manual are the ones `make scout` cannot do for you (it syncs, daemon-reloads, and
enables units, but never starts services or pastes the salt). Everything else is
`make scout`.

### 0.1 Prerequisites (manual, once)

- `loginctl enable-linger` (sudo-free, polkit-mediated).
- `uv tool install 'litellm[proxy,extra_proxy]==1.106.6'` (pin; prisma/psycopg live
  only under `extra_proxy`).
- Prime Prisma after the install (once per machine / per reinstall):
  `PATH=$HOME/.local/share/uv/tools/litellm/bin:$PATH prisma generate --schema
  $HOME/.local/share/uv/tools/litellm/lib/python3.13/site-packages/litellm/proxy/schema.prisma`
  and the same for `.../litellm_proxy_extras/schema.prisma`. Without this,
  `scout-litellm.service` fails at startup with "Unable to find Prisma binaries".
- `LITELLM_SALT_KEY` from your password manager (never rotate after models are added).
- Optional upstream keys (`OPENROUTER_API_KEY`, …) if you want to call those models.

### 0.2 First deploy

```
cd /home/mike/linux_setup
make scout
```

Generates `~/.config/scout/pg.env` + `scout.env` (mode 600), syncs descriptors,
config, and units, daemon-reloads, and enables everything (no services started).
The salt is still the placeholder at this point — that is allowed on first creation.

### 0.3 Paste the salt, then re-deploy

Replace `LITELLM_SALT_KEY=__SALT_PLACEHOLDER_REPLACE_ME__` in
`~/.config/scout/scout.env` with the real value, then:

```
make scout
```

The secrets guard now passes; re-sync + enable.

### 0.4 Start the substrate (manual — make scout only enabled)

```
systemctl --user start scout-pgdb.service
# wait for healthy:
systemctl --user is-active scout-pgdb.service
podman ps --filter name=scout-pgdb

systemctl --user start scout-litellm.service
journalctl --user -u scout-litellm.service -n 20
```

Smoke (master key from `scout.env`):

```
K=$(grep '^LITELLM_MASTER_KEY=' ~/.config/scout/scout.env | cut -d= -f2-)
curl -sS -H "Authorization: Bearer $K" http://127.0.0.1:4000/model/info | head
```

### 0.4a Enable a keyed provider (optional, before §0.5)

Default descriptors ship `auth: none`, so the first cycle syncs with no secrets.
How to make a provider's models callable depends on what the provider exposes.
Examples: OpenRouter (keyed, with a LiteLLM-documented name) and Kilo (keyless,
anonymous-only — its gateway returns `401` for any non-empty bearer).

#### Step 1 — Check whether LiteLLM documents the provider

Open https://docs.litellm.ai/docs/providers in a browser. If the provider is
listed, its page states the env var name outright (OpenRouter:
`OPENROUTER_API_KEY`) — reuse that name for consistency. If it is not listed,
LiteLLM has no dedicated handler (Kilo is not listed); the provider rides the
generic `openai/`-compatible path and has no hardcoded name — you invent one.

Fallback without a browser (one-liner over the installed package):

```
grep -rnE 'def _complete_<provider>|<PROVIDER>_API_KEY' \
  ~/.local/share/uv/tools/litellm/lib/python3.13/site-packages/litellm/main.py
```

Hit → a hardcoded name exists. No hit → none.

#### Step 2 — Wire the key through scout (generic recipe)

1. Pick the name: LiteLLM's documented one (step 1), or your own
   `<PROVIDER>_API_KEY` (arbitrary; only consistency matters).
2. Make it available to the proxy:
   `echo 'NAME=...' >> ~/.config/scout/scout.env` +
   `systemctl --user restart scout-litellm.service`.
3. Descriptor (repo copy): `auth: env/NAME`.
4. `make scout`.
5. Never leave the key empty: empty is falsy and falls back to
   `OPENAI_API_KEY`; for a genuinely keyless provider (Kilo) keep `auth: none`
   — scout then sends a space bearer, which such gateways accept.

Refs resolve at load/reload time, not per request; restart or reload after
changing the env var. Direct `litellm.completion()` calls do not resolve refs.

**Auth changes are auto-recycled.** The snapshot records each provider's auth
form at last sync; changing `auth` in a descriptor re-registers that provider's
models (delete + re-POST) on the next cycle — report line:
`- auth-changed <provider>: <old> → <new> — N models re-registered`.
One-time caveat on registries synced before this field existed (list-form
snapshots record no auth): those entries keep their old key until a manual
refresh — delete them via `POST /model/delete` and prune the ids from
`catalog.snapshot.json` once, then run scout. Every later auth edit is
hands-off.

Do this before §0.5 or the provider's models register but fail at chat time
(triage row: "Keyless provider chat fails").

### 0.5 Run the scout cycle (manual start)

```
systemctl --user start scout.service
journalctl --user -u scout.service -n 30
tail -n1 "$(ls -t ~/.local/state/scout/report-*.md | head -1)"
```

Expected first run: `scout run: changed=True sync=applied`, report verdict
`verdict: clean`, and `/model/info` now lists `kilo/*` and `openrouter/*` entries.
The snapshot advances only because sync succeeded.

### 0.6 Arm the timer (manual start — make scout only enabled)

```
systemctl --user start scout.timer
systemctl --user list-timers scout.timer
systemctl --user status scout-reloader.path
```

### 0.7 Verify a model end-to-end

```
K=$(grep '^LITELLM_MASTER_KEY=' ~/.config/scout/scout.env | cut -d= -f2-)
curl -sS http://127.0.0.1:4000/v1/chat/completions \
  -H "Authorization: Bearer $K" -H 'Content-Type: application/json' \
  -d '{"model":"kilo/nvidia/nemotron-3.5-super-vl:free","messages":[{"role":"user","content":"ping"}]}' | head -c 500
```

Expect a JSON response with `choices` (Kilo models need no upstream key).

### 0.8 Add a provider

See §2 for the full walkthrough. Minimal loop:

1. Drop/edit a descriptor in the repo copy: `scout/providers/<name>.yaml`.
2. `make scout` (syncs; the path unit hot-reloads within seconds).
3. Wait for `scout.service` to run; confirm new IDs in `/model/info`.

Repo truth rule applies throughout: edit under `scout/`, then `make scout`. Editing
the live copy under `~/.config/scout/` silently forks truth.

### 0.9 Where next

- Triage: §4. Verification commands: §6. Limitations: §7.

---

## 1. Control interface

The control plane is two file groups, watched by the `scout-reloader.path` unit
via `PathChanged` on `~/.config/scout/providers` + `~/.config/scout/blacklist.yaml`:

- Add/change/remove a provider: create/edit/delete one descriptor in
  `~/.config/scout/providers/<name>.yaml`.
- Blacklist editing: edit `~/.config/scout/blacklist.yaml` (both tiers). Resolution
  order per model: global → per-provider → free detection → capability filter.

**Repo truth rule:** edit the repo copy under `scout/`, then `make scout`. Editing
the live copy silently forks truth (the ADR-006 heresy). The path unit hot-reloads
within seconds of the sync, so deploy + reload are one gesture.

CLI (binary `~/.local/bin/scout`):

- `scout validate [file…]` — contract check, no fetch, no writes; exit 0/1.
- `scout diff [--provider X] [--providers-dir DIR] [--blacklist FILE] [files …]` —
  dry-run fetch + predicate chain; exit 0/1. Touches neither LiteLLM nor the snapshot.
- `scout run [--provider X] [--providers-dir DIR] [--blacklist FILE] [files …]` — full
  cycle: fetch, diff, sync, snapshot, report; exit 0/1.

Exit codes: `0` clean; `1` contract error (printed `file:line:col: message`, plus the
offending line) or a runtime load error; `2` no files given and no `providers/*.yaml`
found (also argparse usage errors).

Defaults are **relative to the current directory** (`providers/`, `blacklist.yaml`), so
interactive runs must pass the absolute paths the unit uses, e.g.

```
scout validate --providers-dir ~/.config/scout/providers --blacklist ~/.config/scout/blacklist.yaml
```

The unit itself invokes `scout run --providers-dir %h/.config/scout/providers
--blacklist %h/.config/scout/blacklist.yaml` with `Environment=SCOUT_TRIGGER=systemd`.

## 2. Provider walkthroughs

- **Add:** write the descriptor into the repo copy (`scout/providers/<name>.yaml`),
  run `make scout`, wait for the path unit to trigger `scout.service`. Verify the new
  model IDs appear via LiteLLM's `/model/info` (see §6).
- **Change:** edit the descriptor, `make scout`; the path unit re-runs the cycle.
- **Remove:** delete the descriptor from the repo + `make scout`. A gone provider is
  **not** tombstoned: its models are removed from the registry and re-addition
  auto-restores. Report line:
  `## provider-removed: <name>` followed by
  `<name>: descriptor gone; models removed from registry, NOT tombstoned; re-addition auto-restores`.
- **Blacklist:** edit `scout/blacklist.yaml` (global = blocked on every provider; providers
  = per-descriptor keyed by `kilo`/`openrouter`). Header semantics (verbatim):
  `# Two-tier blacklist. Edit this file directly — the path unit hot-reloads it.`
  `# global: model ID blocked on every provider (bare id or provider/id form).`
  `# providers: per-provider, keyed by descriptor name (kilo, openrouter).`

## 2a. Deletion semantics

- **Model vanished from a provider's catalog** → removed from the LiteLLM registry,
  logged, and appended to `tombstones.json` (a flapping model that reappears
  surfaces as `- flap <provider>/<id> — tombstone re-entered`).
- **Provider file deleted** (`providers/<name>.yaml` gone) → its models are removed
  from the registry and logged as `## provider-removed: <name>`; **not** tombstoned,
  so provider re-addition auto-restores. The two removal types get distinct labels.
- **Blacklisted model reappears** → blocked again by the blacklist, logged.

## 3. Key provisioning (three tiers)

Machine-local creds are generated on first deploy by `scout/deploy/scout-secrets.sh`
(create-if-absent, never overwrite, mode 600):

- `~/.config/scout/pg.env` — `POSTGRES_USER=litellm`, `POSTGRES_DB=litellm`,
  `POSTGRES_PASSWORD=$(openssl rand -hex 32)`, consumed by `scout-pgdb.container`.
- `~/.config/scout/scout.env` — `LITELLM_MASTER_KEY=sk-$(openssl rand -hex 32)`,
  `DATABASE_URL=postgresql://litellm:<pw>@127.0.0.1:5432/litellm`, plus
  `LITELLM_SALT_KEY`, consumed by `scout-litellm.service` and `scout.service`.
- Generated creds are regenerable on a fresh machine; values never enter the repo.

Postgres runs as a rootless podman user quadlet: pinned image
`docker.io/library/postgres:17`, named volume `scout-pgdb`, loopback-only publish
`127.0.0.1:5432`, `Restart=on-failure` in `[Service]`, `TimeoutStartSec=300`,
`ContainerName=scout-pgdb`, `VolumeName=scout-pgdb` (pinned so the secret guard can
check `podman volume exists scout-pgdb`). Auto-start requires `loginctl enable-linger`
(operator step, polkit-mediated, sudo-free).

**Manual, before first install (operator, never automated):**

- `LITELLM_SALT_KEY` — paste from the password manager into `scout.env`. The generator
  refuses while the placeholder remains, printing:
  `scout-secrets: ERROR LITELLM_SALT_KEY is still the placeholder in …/scout.env.`
  `scout-secrets: ERROR Paste the real salt from your password manager into LITELLM_SALT_KEY=, then re-run 'make scout'.`
  (placeholder literal: `LITELLM_SALT_KEY=__SALT_PLACEHOLDER_REPLACE_ME__`).
- `loginctl enable-linger` (sudo-free, polkit-mediated).
- `uv tool install 'litellm[proxy,extra_proxy]==1.106.6'` (pin; prisma/psycopg live only
  under `extra_proxy`).
- Upstream provider keys (`OPENROUTER_API_KEY`, `KILO_API_KEY`, …) — configured
  manually inside LiteLLM via `~/.config/scout/config.yaml` (`litellm_params.api_key:
  os.environ/NAME` refs), or the same refs sent by the scout in `POST /model/new`.
  The scout never holds raw upstream keys; its env stays empty of them permanently.
- Hardcoded vs generic: see §0.4a. Check https://docs.litellm.ai/docs/providers
  for the provider's documented env var (reuse it when present); otherwise
  invent `<PROVIDER>_API_KEY`. Kilo is not documented and is anonymous-only,
  so it stays `auth: none`.

Guard: if `pg.env` is missing while the Postgres volume already exists (`podman volume
exists scout-pgdb`), the generator hard-fails rather than regenerating:
`scout-secrets: ERROR pg.env is missing but the Postgres volume 'scout-pgdb' already exists.`
`scout-secrets: ERROR Restore …/pg.env from backup; do NOT regenerate (password would no longer match the volume).`

## 4. Triage table

| Fail state | Signal source | Fix |
|---|---|---|
| Lock contention | stdout/journald: `scout: flock contention — waiting for lock` | Wait; flock blocks, does not skip. |
| Contract error on a descriptor | `scout validate` / run stderr + `file:line:col: message` | Fix the descriptor; validate exits 1. |
| Provider returned 0 models (indistinguishable from empty) | stderr `  WARN: provider returned 0 models (indistinguishable from empty catalog)`; report `…: 0 models returned (indistinguishable from empty catalog)` | Check base_url/catalog endpoint/auth; loud-warning by design. |
| Provider yielded 0 free after chain | stderr `  WARN: provider yielded 0 free models after predicate chain`; report `…: 0 free models after predicate chain` | Review rules/blacklist/keywords. |
| Fetch failure | stderr `  ERROR fetch: <e>`; report `<name>: fetch failed: <e>` | Upstream outage; retry next run. |
| Sync failure against LiteLLM | stderr `  ERROR sync add <reg>: <e>` / `  ERROR sync delete <reg>: <e>`; summary `scout run: changed=True sync=failed (retry next run)`; snapshot does NOT advance | Check `SCOUT_LITELLM_URL` (default `http://127.0.0.1:4000`) + `LITELLM_MASTER_KEY`; `/model/info` reachability. |
| LiteLLM info fetch failure | stderr `scout: WARN litellm GET /model/info failed: <e>` | Start `scout-litellm.service`. |
| Salt placeholder present | `make scout` aborts at `secrets` target (messages above) | Paste the real salt. |
| Missing pg.env with existing volume | `make scout` aborts at `secrets` target (message above) | Restore `pg.env`; never regenerate. |
| Quadlet unit missing after `make scout` (`scout-pgdb.service does not exist`) | `systemctl --user show-environment` shows `XDG_CONFIG_HOME=%h/.config` (literal) | `environment.d(5)` does not expand `%h`; fix `dotfiles/environment.d/xdg.conf` to use `${HOME}`, re-deploy (`make dotfiles`), `systemctl --user daemon-reload`, then re-run `make scout`. |
| LiteLLM fails at startup: "Unable to find Prisma binaries" | `journalctl --user -u scout-litellm.service` | Run `prisma generate` for both schemas (see §0.1). |
| Keyless provider chat fails: "Missing credentials … OPENAI_API_KEY" | `/v1/chat/completions` returns 500 for a `kilo/*` model | Re-sync after upgrading scout (it now stores a space key for `auth: none`); or set `OPENAI_API_KEY=' '` in the service env. |
| Auth change not applied to existing models (pre-recycle entries) | Snapshot entry is list-form (no `auth` key) while models stay space-keyed after an `auth:` edit | One-time manual refresh: `POST /model/delete` each affected id + prune ids from `catalog.snapshot.json`, then run scout. Later auth edits auto-recycle. |
| A model re-appears after removal | report `- flap <provider>/<id> — tombstone re-entered` | Investigate upstream catalog churn (tombstone flap). |
| Model add in a run | report `- add <provider>/<id> — <badge>` | Badge ∈ {suffix, pricing_zero, flag, assumed}; reflects admission rule. |

Verdict lines (last line of `~/.local/state/scout/report-YYYYMMDD.md`):
`verdict: clean (no changes)` / `verdict: warn — N warning(s)` / `verdict: clean`.

## 5. Journal / debugging

Journald via stdout/stderr of the unit: `journalctl --user -u scout.service`,
`journalctl --user -u scout-litellm.service`. Rotation is journald's, not ours. Log metadata
(status codes, error classes) only — never upstream keys or completion bodies.

Reports are the decision log (`~/.local/state/scout/report-YYYYMMDD.md`); snapshot is
machine state; tombstones are observation memory; `catalog.display.json` is a
write-only display artifact (model id + badge per admitted model), not read by scout.
No CHANGELOG.md.

## 6. Verification

- Units: `systemctl --user status scout.timer scout.service scout-litellm.service`.
- XDG: `systemctl --user show-environment | grep XDG_CONFIG_HOME` must be an absolute
  path (`/home/…/.config`); a literal `%h/.config` breaks quadlet generation.
- Prisma: `ls $HOME/.local/share/uv/tools/litellm/lib/python3.13/site-packages/prisma` non-empty.
- Postgres: `podman volume exists scout-pgdb` + `scout-pgdb.service` active.
- Registry: `curl -H "Authorization: Bearer $LITELLM_MASTER_KEY" http://127.0.0.1:4000/model/info`
  (live smoke belongs to the operator acceptance block, not this doc).
- Validate descriptors: `scout validate --providers-dir ~/.config/scout/providers`.
- Last report verdict: `tail -n1 "$(ls -t ~/.local/state/scout/report-*.md | head -1)"`.

## 7. Known limitations

- Upstream keys are manual; no secret-management infra.
- No liveness probing in v1: catalog truth comes from the diff; serve-time death is
  absorbed by LiteLLM cooldowns/fallbacks. Promote a probe only if deprecated models
  linger in free lists long-term; LiteLLM cooldown logs are the interim evidence.
- Emitting catalogs for other gateways (OmniRoute/FreeLLMAPI formats) is deferred;
  trigger is an actual gateway switch away from LiteLLM.
- No sudo in `make scout` or any Makefile target; if a step ever needs privileges,
  the whole chain moves into `lib/` + `install.py`.
- Not automated: scout is not in install.py's step table and is not part of
  `make all`; deploy via `make scout` after completing the convergence block
  (salt paste, linger, uv tool install, prisma priming, live smoke). See
  backlog 018.
- `packages/uv_tools.txt` list type is parked (backlog 017); `uv tool install …` is
  currently a manual operator step.
- LiteLLM version pinned to `1.106.6` (pinned); bump only after checking the
  release's "Action required" notes (prisma/psycopg under `extra_proxy`).