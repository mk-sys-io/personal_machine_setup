# gopass — API-key management for AI-agent workflows

gopass is the **single, no-fallback** API-key store for `provider_registry` and
its consumers (`pi_setup`, future agents). No plaintext vault file,
no env-var fallback. Keys live in encrypted gopass entries; agents get secrets
via `gopass env` injection so values never land in a context window or on disk.
(NOTE 2026-09-15: `tools/ask.py` retired to `plans/archive/ask.py`; its Pattern 1
`ask serve` daemon is gone. The future in-engine SearXNG AI-answers plugin will
be the next Pattern 1 consumer — see
`plans/active/desktop/searxng-custom-ui-ai-answers.md`.)

## Quick start

```sh
gopass setup                                        # one-time, interactive
gopass insert provider-registry/<id> key            # add an API key
gopass insert provider-registry/<id> strategy       # pi_setup only: probe|fetch
gopass show provider-registry/<id> key              # verify
```

Entry layout: `provider-registry/<id>` with a `key` field (the API key) and an
optional `strategy` field (pi_setup's probe/fetch override). Rotation = re-run
`gopass insert`. First use triggers gpg-agent pinentry (interactive).

## Architecture

- **`provider_registry`** — shared: provider registry (identity, endpoints,
  chat contract), the chat adapter, and the single gopass accessor
  (`config.py::gopass_show_field`).
- **`pi_setup`** — Pi-specific: probe/fetch engines + config, manifest, and the
  credential copy to `~/.pi/agent/auth.json`.
- **`gopass`** — owns credential storage.

## Communication patterns

How a module obtains secrets from gopass — decided by its **lifecycle** and
whether it **already has a credential location**.

- **Pattern 1 — Per-request read.** Shell out to `gopass show <entry> <field>`
  each time a secret is needed; used immediately and discarded, nothing
  persisted or injected into the process env. For long-running consumers
  (daemons/servers) or ones that switch providers dynamically — env injection
   would hold the secret resident in the process env for its whole lifetime.
   Former consumer: retired `ask serve` daemon. Next consumer: the in-engine
   SearXNG AI-answers plugin (Pattern 1 per-request reads).
- **Pattern 2 — Copy to an existing credential location.** The module has a
  fixed credential file its runtime reads from; gopass is the *source*, read
  once at setup and copied there. The plaintext copy is an accepted,
  intentional deployment target — used only for pre-existing credential-file
  contracts that can't read gopass directly. → `pi-setup auth` →
  `~/.pi/agent/auth.json`.
- **Pattern 3 — gopass as the live source (`gopass env`).** The module has no
  credential location — gopass *is* its store. Launched as a child of
  `gopass env <entry> -- <cmd>`; gopass decrypts once at launch and injects the
   secret into the subprocess env. For short-lived, launchable consumers with no
   existing credential file — the default for new modules. Former consumer:
   retired `ask` CLI one-shot.

**Decision rule for a new module:** already has a credential location →
Pattern 2; short-lived/launchable under gopass → Pattern 3; long-running daemon
or dynamic switching → Pattern 1.

### Read costs and refresh behavior (added Phase 12-B)

- **Pattern 1 at machine frequency:** long-running consumers that cannot
  tolerate a decrypt per request read once at startup into daemon memory
  and reuse the value for the process lifetime. This trades freshness for
  latency — acceptable only where rotation mid-lifetime is out of scope.
- **Cost model is machine-dependent:** a cold read pays a full decrypt
  plus a pinentry prompt on first use; warm reads with a live gpg-agent
  are a fraction of a second. Measure on the target machine with
  time gopass show ENTRY FIELD (cold: fresh login or reloaded agent;
  warm: repeated reads). Do not copy timings between machines as fact.
- **Pattern 2 copies go stale:** the plaintext at the credential location
  is a point-in-time copy. After rotation, refresh by re-running the copy
  step (for example pi-setup auth); nothing refreshes it automatically.

## Failure modes

| FM | Condition | Behavior |
|----|-----------|----------|
| FM1 | gopass binary not installed | Hard error: `run install.sh (lib/20-packages.sh installs it)` |
| FM2 | store not initialized | Hard error: `run: gopass setup` (no silent auto-init) |
| FM3 | `show` read failure | exit 10 (missing) → benign `None`; exit 11/18 (decrypt/IO) → hard error |
| FM4 | `insert` write failure | exit 3 (declined overwrite) → benign; exit 12/18 → hard error |
| FM5 | `rm` delete failure | exit 10 (not found) → benign no-op; exit 18 → hard error |
| FM6 | `ls` listing failure | exit 13 → hard error; exit 6 → FM2 |
| FM7 | gpg-agent pinentry | Do not suppress stdin/stdout; no `--yes`/`-n` flags |

Hard errors raise `ToolError` with the gopass stderr plus a human-readable
"why + how to fix". The current accessor (`gopass_show_field`) exercises FM1,
FM2, FM3, FM7 (show-only); FM4–FM6 are reserved for future insert/rm/ls paths.