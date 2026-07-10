# Observations & Retrospectives

## Overview

This file captures issues, gaps, and patterns observed during the build of the White Internet Policy setup (Phases 1–4). It is not a plan — it is a record of what went wrong, what was missed, and what could have been done better.

These are notes for future reference, not action items.

---

## Gaps in the Current Setup

### No linter integration

None of the scripts are run through ShellCheck, no markdown files are linted with remark/prettier, and the JSON policy templates are never validated against Chrome/Firefox policy schemas.

Result:
- The `-e` flag omission on `tle` was caught at runtime, not at write-time
- Inconsistent quoting and `set -e` patterns across scripts
- No way to verify policy JSON is valid before it gets deployed to `/etc/`

### No opencode skills or session memory

There is no opencode skill defined for this project. Every session starts without institutional memory, and the summary at the top of each session has to be manually maintained.

Result:
- Repeated exploration of the same code
- Context is re-explained each session
- No automatic reuse of patterns

### No structured validation beyond verify.sh

`verify.sh` tests runtime behavior (is DNS blocked? is nftables rule present?) but never validates:
- That the generated JSON is syntactically valid
- That `nftables.conf.locked` is syntactically correct
- That no stale policy files linger (e.g., `kiosk_policy.json` in `/etc/chromium/`)
- That `allowlist.txt` contains only valid domain names
- That the NextDNS config ID matches the expected format

### No dry-run or preview mode

`lock`, `seal`, and `unlock` are all-or-nothing. There is no way to preview what would happen before doing it.

Relevant failures:
- The 404 recovery URL was only discovered when someone tried to use it
- No way to verify the recovery command before seal locks the system
- No way to preview which domains would be whitelisted before locking

---

## Unknown Unknowns — How They Were Missed

Things we did not know we did not know, and how they surfaced:

| Issue | How it was discovered | Could it have been caught earlier? |
|---|---|---|
| `tle -e` flag required | Hit at runtime — `tle` errored with "only one of -m/--metadata, -d/--decrypt or -e/--encrypt must be passed" | Yes — had we run `tle --help` before integrating it, or read the tle README more carefully |
| `tle-linux-amd64` binary URL returned 404 | Hit at runtime — the bare binary asset does not exist for v1.2.0, only the tarball | Yes — had we verified the URL before documenting it |
| `skuid` nftables rules only work from the matching UID | verify.sh test 5 ran as root (UID 0) and passed, but mike (UID 1000) was still blocked | Tricky — requires testing from the right user context, which is easy to overlook |
| Podman rootless bypasses host nftables | Discovered when verify.sh test 7 failed in locked mode | Hard to anticipate without knowing podman's network model |
| Firefox policies require `/etc/firefox/policies/policies.json` not `policies.json` under `/etc/firefox/` directly | Trial and error after policies were not showing up in `about:policies` | Could be caught by reading Firefox enterprise docs, but easy to miss |
| PolicyKit on Debian 13 uses JavaScript `.rules` not `.pkla` | The `.pkla` file was silently ignored | Yes — Debian 13 documentation states polkit ≥0.106 drops `.pkla` support |
| `sudo` group removal requires all post-sudo operations via `su -` | Only surfaced when trying to run allowlist commands after `gpasswd -d mike sudo` | Intellectually predictable, but easy to miss in the workflow design |
| `tle` not found when running as root (different `$PATH`, different `$HOME`)| Runtime failure in `seal` from `su -` | Yes — root and user `$PATH` differ, should always use absolute paths or `/usr/local/bin/` |

---

## Patterns That Worked

Despite the gaps, some approaches were effective:

### Conflict hunting with the LLM

Explicitly asking "find conflicts, edge cases, and inconsistencies" between files caught:
- `dee` → `tle` inconsistencies in `internet.yaml` and docs
- `kiosk_policy.json` stale file conflict with new policy structure
- Missing `-e` flag on `tle` was eventually caught (after runtime failure first)
- Workspace volume mount path in recovery command
- `sudo` usage inside `verify.sh` when running as root
- SUDO_USER environment variable assumptions in `seal`

These were found by explicitly sending related files together and asking what does not match. The process, however, was reactive — issues were found after they were already in the code.

### Session summaries

Maintaining a structured summary of progress, decisions, and remaining work at the top of the conversation helped:
- Avoid repeating failed approaches
- Maintain context across multiple rounds of iteration
- Catch when a decision was made but not reflected in code

The summary had to be manually updated, which was fragile.

### Verification script as regression test

`verify.sh` caught runtime regressions (e.g., when `nftables` service restarts, when `nextdns` is not running, when polkit rules are missing). It is not comprehensive, but it is better than nothing.

---

## What to Avoid Going Forward

1. **Never document a command or URL before verifying it works.** The 404 recovery URL and the `tle -e` flag are both examples of documenting assumptions instead of facts.

2. **Always test from the same user context as production.** Running verify.sh as root gave false confidence about DNS blocking working correctly for user mike.

3. **Validate generated output, not just runtime behavior.** Checking that the deployed JSON is valid is as important as checking that DNS is blocked.

4. **Do not rely on `$HOME` or `$PATH` for root operations.** Use absolute paths, `/usr/local/bin/`, or `getent passwd` to locate user files.

5. **A dry-run mode is not optional for destructive operations.** `lock` (breaks browsing) and `seal` (deletes credentials) are permanently destructive. A preview would have caught at least the recovery URL issue.

---

## Questions Still Open

- Should the template engine (`sed`-based substitution) be replaced with something that validates output (e.g., `jq` to build JSON)?
- Should `allowlist.txt` be validated on `add` to reject malformed domains?
- Should the project have a proper CI check (ShellCheck, `nft -c`, `python3 -m json.tool`) even though it is a personal setup?
- Should there be an automated migration path for breaking changes, or is each run a fresh install?

---

## Bash vs Ansible — Observations

### Current approach (bash install.sh)

Strengths:
- Single file, no dependencies — runs on a bare system
- APT array + `dpkg -s` loop approximates Ansible's idempotence
- `set -euo pipefail` catches unexpected failures early

Weaknesses:
- One failed `apt` call aborts the entire script (mitigated by the loop, but still brittle)
- Error output is raw stderr — grep/dig through to find the actual problem
- `sudo` sprinkled everywhere; breaks when sudo is later removed, requiring `su -` workarounds
- No resume — fix the issue, re-run from the top
- Guard logic (`command -v`, `dpkg -s`, `[ -x ]`) is manual and easy to get wrong (e.g. `localsend` binary not in PATH)

### Ansible approach (bootstrap + playbook)

Strengths:
- `apt: state=present` is inherently idempotent — no manual guards needed
- Tasks are independent — one failure doesn't abort the rest
- Structured per-task output with diffs and error messages
- `--start-at-task` resumes from any point after a failure
- `become: yes` replaces scattered `sudo` — clearer and more flexible
- YAML is declarative; easier to read than conditional shell logic

Weaknesses:
- More files (~10+ vs 1) — a playbook, vars, and task files per concern
- Python dependency — must install Ansible first, which pulls in Python deps
- After sudo removal, still requires `su -` to run (same as bash)
- Overkill for a single-machine personal setup

### Verdict

For a single machine, the current bash script with the APT array pattern is at a
good complexity level. Ansible would be 10+ files for marginal benefit at this
scale. Keep bash unless managing multiple machines or wanting to learn Ansible
on this project.

---

## Dotfile Management (GNU Stow)

### Current approach

`install.sh` has ~15 `mkdir` + `cp` lines that copy config files from the repo's
`.config/` directory to `~/.config/`. Each new app (Zed, foot, Obsidian, etc.)
adds 2 more lines.

### Stow approach

GNU Stow creates symlinks from a structured directory into `$HOME`. The repo
already mirrors `~/.config/`, so running `stow -t ~ .` from the repo root would
replace all the `mkdir`+`cp` boilerplate with a single command.

### Tradeoffs

| Aspect | Stow | Current (cp) |
|---|---|---|
| Boilerplate | 1 line after setup | ~15 lines and growing |
| Edit workflow | Edit repo file → live symlink | Edit repo → re-run install or re-copy |
| Root-owned files (e.g. `/opt/`, `/etc/`) | Not supported | Same — already uses `sudo cp` |
| Non-standard paths (e.g. Obsidian vault) | Needs `--target` per path | Handled manually |
| Learning curve | Must learn Stow | Works immediately |

### Verdict

Stow would clean up `install.sh` but adds a dependency and doesn't help with
root-owned files or non-standard paths. Worth adopting if config files keep
growing; overkill at the current ~15-file scale.

---

## Issue tracking format: markdown file vs specialized software

### Context

The project uses a flat `issue_tracker.md` for tracking bugs, feature requests, and design discussions. This raised the question of whether a specialized tool would be better.

### Tradeoffs

| Aspect | `issue_tracker.md` | GitHub Issues / Linear |
|---|---|---|
| Zero dependencies | Yes — any text editor, no account needed | Requires account, web UI or CLI tool |
| Lives with code | Git-tracked alongside config files | Separate database, needs sync |
| Filter/search | `grep` by status/tag/number | Built-in search, labels, milestones, assignees |
| Collaboration | Manual — share the file | Built-in — comments, mentions, notifications |
| CLI access | `grep`, `awk`, or hand-rolled scripts | `gh issue list --state=open --label=bug` |
| Scales past ~50 issues | Becomes unwieldy | Handles thousands |

### Verdict

For a single-user dotfiles project, `issue_tracker.md` with grep-based filtering is the right level of complexity. The natural upgrade path is a local/private GitHub repo with `gh issue list` from the terminal — not because the file breaks, but because the built-in filtering (labels, milestones, state) saves time once the list grows past ~50 entries.
