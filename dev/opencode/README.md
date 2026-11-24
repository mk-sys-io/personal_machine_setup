## MCP rationale

### Context7 — always on

The agent hallucinates library APIs from stale training data.
Context7 fetches current version-specific docs at query time.

## Plan Workflow: `:run`

Multi-file features use the `:run` espanso trigger to execute the plan. Switch to build mode manually before firing it.

**Setup per repo:** `tools/init.sh` creates `plans/` + `.ignore` (`!plans/` enables `@plans/filename` references). Plans are excluded from git via `~/.config/git/ignore` (global). See [plan-workflow.md](docs/plan-workflow.md) for the full guide.

## `/tmp` lockdown (opencode.jsonc)

Global V2 `permissions` array, last-match-wins: catch-all first (`ask` for build, `allow` for plan/explore/general), `/tmp` denies next, `/tmp/opencode` allows last. `/tmp` outside `/tmp/opencode` → hard `PermissionDeniedError` whose ruleset JSON names the allowed path — never a prompt; `/tmp/opencode` ($TMPDIR/opencode, auto-created) → silent.

**Reasoning:** constant `/tmp` prompts are pure friction (#28173); on a single-user box this is hygiene, not security — bash bypasses path policy anyway (#18396) and prompt guardrails are "wet tissue paper" per maintainers (#21684). The dedicated subdir also avoids stray-file collisions (#28089). Deny-not-ask because subagents can't answer prompts — they stall silently today. The chain repeats in plan/explore/general: their broad `external_directory` allow rules append after global rules and win last-match; session derivation then covers all future subagents (never add a broad `external_directory` allow rule to new agents).

**Secrets denylist:** global `read` + `edit` chains deny `ssh`, `gnupg`, `aws`, `.config/gopass`, `.password-store`, `kube`, `.config/gcloud` (read = exfil vector, edit = `authorized_keys`-overwrite vector). Each dir gets three rules — bare dir (`*/.ssh`), hardcoded dir (`/home/mike/.ssh`), contents (`*/.ssh/*`) — because a content pattern needs ≥1 char after the slash, so `*/.ssh/*` does not match the directory itself and `read` on a directory lists it (verified leak). Leading `*/` is load-bearing: `read`/`edit` match worktree-relative paths (PR #26583) while `external_directory` matches absolute, so anchored `~/...` patterns can never match here — `*/` hits both forms (`*` crosses `/`; `**` is still accepted in V2 but cosmetic). The `/home/mike/...` pair is redundant with `*/` by design: it makes the single-user assumption legible in-file. That assumption is enforced by `config.txt` `USERNAME=mike` + `lib/helpers/common.sh:121`, which aborts the deploy if `whoami != USERNAME`, so a differently-named account fails loud at install rather than silently half-wired; gomplate-rendered `/opt/ark` files bake the literal the same way. Changing `USERNAME` means editing these paths too. The global `read` allow-all rule replaces built-in defaults, so `*.env` / `*.env.*` denies are re-listed explicitly (`*.env.example` re-allowed); `*.envrc` is denied in **both** chains — it matches neither built-in pattern, and direnv sources it as shell code on `cd`, so writing one is worse than reading one. Per-agent broad `read` allow rules were deleted so every agent inherits the global chain — single copy, no drift. Per-agent `external_directory` chains stay (`*` allow is intentional roaming for subagents vs `*` ask globally); plan additionally keeps an `edit` deny (V2 folds `write`/`patch` into `edit`), which overrides the global edit chain.

**Known holes (parked, `docs/backlog/backlog.md` 005/006):** these chains are the cheap layer, not a complete boundary. `grep` permissions match the _regex_, not the matched file, so `grep` prints secret contents regardless of the `read` chain (upstream #17073); permission matching never resolves symlinks, so a symlink under `/tmp/opencode` reaches any secret (upstream #51347); `bash` bypasses all of it (advisory extractor, #18396). Also note the per-agent roaming makes the global `external "*" ask` void inside plan/explore/general — agent rules merge after global and win last-match — so the read/edit chains are subagents' only guard.

**Limits:** bash redirections/side-effect writes (`echo x > /tmp/y`) slip through — path extraction is advisory only (#18396, #32628); no OS sandbox exists to close this (accepted). Glob-matcher bugs (#20045, #30551) fail safe to a prompt, never a silent allow. No custom error text — steering = ruleset JSON + AGENTS.md scratch section + per-call bash description. Verify after `make dev` + auto-reload (`/reload` to force): `write /tmp/x` → error, `write /tmp/opencode/x` → silent, subagent `read /tmp/x` → error, `bash echo > /tmp/y` → known slip. Secrets: `read ~/.ssh` (bare dir) → error, `read ~/.ssh/config` → error, `read ../.ssh/config` (relative form) → error, `read /tmp/x/.envrc` → error, `read .env.example` → silent, `/tmp/opencode` reads still silent.

## Postmortem — opencode research subagent (2026-10)

A custom `researcher` subagent on OpenCode's free tier failed with
"OpenCode's free tier can only be used from within OpenCode" — an upstream
gate affecting custom subagents (#50806, #49723, #49756), reproduced on
opencode 1.18.34 despite the identity-header fix in that release.

Workaround: pin the subagent to a non-Zen model (kilo/nvidia nemotron).
Outcome: the non-Zen fallbacks were unreliable, so the subagent was removed
rather than kept in a degraded state. Research runs through the built-in
websearch/webfetch tools instead. Revisit if upstream fixes the gate.
