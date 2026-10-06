# Backlog — parked future tasks

Portable future-task list. One `## NNN-slug` section per task; numbers never reused.
Append new tasks at the end. Status changes edit only the `status:` line.
Tombstones stay one line. If a drop reason trips the ADR gate
(`docs/adr/README.md`), file an ADR and link it; this file points, never duplicates.

## 001 shell-theme-zsh-catppuccin

- status: parked
- created: 2026-09-17 · review-by: 2027-03-17
- urgency: someday · effort: L
- problem: Login shell is a bash monolith (`dotfiles/bashrc`); no zsh features; no cohesive terminal palette.
- task: Migrate login shell to plain zsh + starship; theme kitty/fzf/starship/Pi in Catppuccin Mocha; bash stays as fallback.
- trigger: resume when active plans queue is clear; un-parks independently.
- notes: P10k rejected (archived 2024); scope terminal + Pi only; prompt = rainbow pills via zsh function (starship can't color per-segment).
- reverify: dead repo-rework/system/README.md Files-table row → enhancement index; Makefile hook per Stream A4.

## 002 nix-home-manager-only

- status: parked
- created: 2026-09-17 · review-by: 2027-03-17
- urgency: someday · effort: L
- problem: 13 install methods across 13 `packages/*.txt` files; no single update path in focused/locked mode.
- task: Replace fragmented installs with Nix + home-manager as the only user-app package manager on Debian base.
- trigger: resume when package-update pain exceeds migration cost; un-parks independently.
- notes: Open proposal (2026-09-03), not locked; single Nix update path replaces the 13-method update idea.
- reverify: dev/pi/skills/nixos/ renames; install.py bootstrap wording; absolute active/… link paths.

## 003 scheduled-backup-timers

- status: parked
- created: 2026-09-27 · review-by: 2027-03-27
- urgency: soon · effort: M
- problem: Backups are manual-only (40-E); a suspended laptop silently skips runs.
- task: Add user systemd timers — daily backup, weekly `forget --prune`, monthly `check` — with 80% quota alert / 90% fail-loud.
- trigger: resume when manual cloud flow (40-E) is stable for 4 weeks.
- notes: Systemd timers over cron (`Persistent=true` catches suspend-missed runs); prune locks repo so weekly, not daily; retention keep-daily 7 / keep-weekly 4 / keep-monthly 6.
- reverify: restic flags against installed version; B2 usage API vs `restic stats` for quota wrapper.

## 004 opencode-v2-migration-eval

- status: parked
- created: 2026-10-02 · review-by: 2027-04-02
- urgency: someday · effort: M
- problem: V1 (1.18.34) is current and maintained, but all new engine work lands in V2; migration is deliberate, not in-place.
- task: Evaluate migrating to OpenCode V2 (separate install, replaces V1 binary); re-check compaction-model pin and subagent resume/steer gaps first.
- trigger: resume when V1 EOL is announced, or a V2-only feature (shared server, steer/queue) outweighs the compaction-model loss.
- notes: V1 plugins do not run in V2 (mandatory port); V2 ignores `agent.compaction.model` (no separate compaction model); keep V1 setup until V2 confirmed.

## 005 opencode-grep-result-permissions

- status: parked
- created: 2026-10-02 · review-by: 2026-11-02
- urgency: soon · effort: S
- problem: `grep`/`glob` permissions match the search *pattern*, not the matched file, so the `read` denylist never filters their output — `grep` in `~/.aws` printed credential lines verbatim (reproduced 2026-10-02). `glob` is repo-scoped and did not leak.
- task: Close content-search exfiltration — either a `tool.execute.before` plugin vetoing `grep`/`glob` whose path resolves into a secret dir, or `"grep": "deny"` outright.
- trigger: resume with 004 (V2 migration) or when #17073 merges a fix; the migration alone does NOT close it.
- notes: Upstream #17073 open since 2026-03-11 (assignee rekram1-node), but fix PRs #35682/#35696 closed *unmerged* — treat as unfixed, not queued. V2 `grep.ts` verified identical (`resources: [input.pattern]`), and v2 `policies` supports only `provider.use`, so neither path helps. Caveat: unverified whether plugin hooks intercept *subagent* tool calls (#5894) — decisive, since subagents are the roaming case.
- reverify: #17073 state; #5894 hook-vs-subagent; whether any grep path-shaped rule can match at all.

## 006 opencode-symlink-permission-bypass

- status: parked
- created: 2026-10-02 · review-by: 2026-11-02
- urgency: soon · effort: M
- problem: Permission patterns match the literal path string and never the real path, so a symlink inside `/tmp/opencode` pointing at `~/.ssh` read the real file (reproduced 2026-10-02). Hard links / bind mounts likely equivalent.
- task: Decide between a `tool.execute.before` plugin calling `realpathSync` on the path arg, dropping the `/tmp/opencode` external allow, moving secrets outside `$HOME`, or an OS sandbox (bwrap/Landlock).
- trigger: resume with 004, or when #51347 lands a realpath fix.
- notes: #51347 open, assignee kitlangton, **no linked PR or branch** as of 2026-10-02; POSIX-only (Windows already realpaths). #40160 open covers the external_directory variant; #43346 closed not_planned. Only an OS sandbox survives `bash`, which bypasses every in-app control (#18396) — so this is mitigation, not a boundary. Plugin TOCTOU remains: realpath-then-open still races.
- reverify: #51347 + #40160 state; #5894 (do plugin hooks intercept subagent tool calls?).

## 007 opencode-p10-batch-bypass

- status: parked
- created: 2026-10-05 · review-by: 2027-04-05
- urgency: someday · effort: M
- problem: P10's bash best-effort regex layer admits false negatives (heredoc bodies, `echo…|python3`, `awk system()`) — batch writes through these paths bypass the branch guard.
- task: Close batch-write bypass — either tighten the regex layer, add an OS sandbox (bwrap/Landlock), or document the limitation as accepted risk.
- trigger: resume when P10 main-branch guard is otherwise ready to ship, or when a bypass is observed in the wild.
- notes: P10 box (opencode-reorder.md:185-188) lists the false negatives in-code; only an OS sandbox survives `bash`, which bypasses every in-app control (#18396). Precedent: 005/006 in this backlog.
- reverify: grep for heredoc/echo-pipe/awk-system patterns in agent output; confirm sandbox feasibility on Debian trixie.

## 008 opencode-p10-toctou

- status: parked
- created: 2026-10-05 · review-by: 2027-04-05
- urgency: someday · effort: S
- problem: Branch-guard plugin's realpath-then-open check is a TOCTOU race — path can change between the check and the actual write.
- task: Close TOCTOU — either accept the race as bounded risk (repo-root scoping limits blast radius), move to an OS-level sandbox, or add a post-write verify-and-revert.
- trigger: resume with 011 (main-guard) or when #51347 lands a realpath fix that eliminates the race.
- notes: #51347 open, assignee kitlangton, no linked PR or branch as of 2026-10-02; POSIX-only. Plugin TOCTOU remains even if upstream fixes path resolution.
- reverify: #51347 state; whether any post-write verify can run without itself being racy.

## 009 opencode-init-override

- status: parked
- created: 2026-10-05 · review-by: 2027-04-05
- urgency: someday · effort: M
- problem: Stock `/init` writes a generic prose-dump AGENTS.md that knows nothing about this repo's router conventions — every new repo starts unrouted.
- task: Create `dev/opencode/deploy/commands/init.md` (same-name override of built-in `/init`): stock `initialize.txt` copied verbatim + preamble (load `agents-md-router` Author mode incl. sibling cookbook read, prefer repo docs broadly over global defaults, reconcile never duplicate, end with `git diff --stat`); `agent: build` mandatory (deliverable is file writes, `plan` agent denies them).
- trigger: resume when repo-onboarding pain exceeds build cost, or when P5 is explicitly re-authorized.
- notes: Build-time gate: diff upstream `sst/opencode` vs `anomalyco/opencode` dev-fork `initialize.txt`, copy verbatim, stop on disagreement. `/init` on a foreign repo must yield a router (drill run from `plan`-mode session to prove `agent: build` escalation fires).
- reverify: `/init` on foreign repo produces router; re-run on good file → minimal diff.

## 010 opencode-git-gates

- status: parked
- created: 2026-10-05 · review-by: 2027-04-05
- urgency: soon · effort: M
- problem: The model can run destructive git ops unchallenged — force-push, `reset --hard`, amend/rebase on pushed commits, blind `git add .`.
- task: Add 5-line style card to global `dev/opencode/deploy/AGENTS.md` (high placement, right after the P8 golden block): conventional `type(scope):` subject ≤50; body why-not-what; atomic commits, never blind `git add .`; no AI attribution trailers; revert-only-on-clean-tree (dirty → stop + report). Add deny/ask patterns in `opencode.jsonc` (last-match-wins ordering: catch-all first, specifics last): DENY force-push ×4 spellings, `reset --hard`, `filter-branch`/`filter-repo`, `clean -fd`, blanket `checkout --`, `stash drop/clear`, `branch -D`, amend/rebase on pushed; ASK plain push (DENY `main`), commit-unless-asked, unpushed-own amend/rebase, pull; explicit ALLOW `fetch`, `revert`.
- trigger: resume when P9 is explicitly re-authorized, or after a near-miss destructive op.
- notes: Deny/ask patterns in `opencode.jsonc` are runtime-loaded, same vehicle as P8's hook. Style card placement is high (after golden block) so it survives compaction.
- reverify: `reset --hard`/`push --force` blocked, plain `push` asks, `revert` passes, style spot-check on a phase-close commit.

## 011 opencode-main-guard

- status: parked
- created: 2026-10-05 · review-by: 2027-04-05
- urgency: someday · effort: L
- problem: The agent writes directly on main — production branch with no guard.
- task: Branch-guard plugin (first-party, ~50 lines, same `make dev` vehicle as P9): scratch-first carve-out (`/tmp/opencode` always allowed, checked before branch); live `git -C <worktree>` branch check per call, never cached; `realpath` targets incl. `apply_patch.patchText` paths; repo-root scoping (outside root → existing chains untouched). STOP text verbatim: stop + report, user decides — names neither branch creation nor the toggle, forecloses bash/python/sed/heredoc/tee routing. Bash best-effort regex layer (`sed -i`, `perl -i`, `awk -i inplace`, `>`/`>>`, `tee`, `python…open(…"w"`, `cat…>`) + deterministic git denylist underneath; documented false-negatives (heredoc bodies, `echo…|python3`, `awk system()`) admitted in-code comments, not prose. `/allow-main` session toggle (`command.execute.before` → in-memory session-keyed Map; restart/config-change resets to guarded) + spawn gate (`task` with writer `subagent_type` on main throws with why; `explore`/`plan` pass; per-tool re-check inside child covers propagation).
- trigger: resume when P10 is explicitly re-authorized, or after an accidental main write.
- notes: 007/008 cover known gaps (batch-bypass + TOCTOU). Experimental hooks may break without notice; remove-don't-degrade on breakage (researcher-subagent precedent).
- reverify: cold session on main — Write denied with STOP text verbatim; `python -c` bypass denied; `/tmp/opencode` write passes; human branch-switch in terminal → agent continues; builder spawn on main throws with why; new session guarded again; `--auto` still blocked; subagent propagation tested + documented.
