# Phase 3 Plan Audit Report

Audit of `archive/rework/phase3-plan.md` (Phase 3 — install.sh restructure).

---

## Critical

### 1. config.env values not exported to child processes

**Status:** Resolved

**Category:** edge-case

The orchestrator sources config.env but never specifies `export`. All modules run as subprocesses via `bash lib/module.sh` and will only see **exported** environment variables. Without `set -a` (allexport) before sourcing, every variable — `USERNAME`, `OPENCODE_PATH`, `GITHUB_TOKEN`, etc. — will be empty inside modules. This is a silent failure mode: modules won't crash (set -e is off in orchestrator), they'll just do nothing useful.

Similarly, `credentials.sh` must export `GITHUB_TOKEN`, `GIT_USER_NAME`, and `GIT_USER_EMAIL` after setting them so downstream modules (packages.sh, github_setup.sh) receive them.

**Mitigation:** Add `set -a` before sourcing config.env in the orchestrator. Add `set +a` after sourcing to stop exporting. Have credentials.sh export its variables.

### 2. NEEDS_REBOOT cannot propagate from hardware.sh subprocess to orchestrator

**Status:** Resolved

**Category:** edge-case

`hardware.sh` runs as `bash lib/hardware.sh` — a subprocess. It sets `NEEDS_REBOOT=true` but this variable vanishes when the subprocess exits. The orchestrator and messages.sh need this variable to decide whether to prompt for reboot. There is no inter-process communication mechanism specified. The old install.sh uses this variable in-process (lines 45, 248, 649). The modular architecture breaks this pattern without an explicit replacement.

**Mitigation:** Use a file-based signal. `hardware.sh` writes a marker file (e.g., `$REPO_ROOT/.install-need-reboot`) when a reboot is needed. The orchestrator or messages.sh checks for the file's existence. Alternatively, parse the exit code (exit 2 = reboot needed) as a convention, or use a shared state file like `/tmp/install-state.sh` that modules append to.

### 3. config.env validation rejects intentionally empty fields

**Status:** Resolved

**Category:** edge-case

The plan states "no empty values allowed" and exits with an error naming the empty key. However, the current `config.env` and `config.env.template` both have intentionally empty fields: `USERNAME=`, `USER_UID=`, `TERMINAL=`, `OPENCODE_PATH=`, `OBSIDIAN_VAULT_PATH=`. `bootstrap-config.sh` fills these via detection, but some may legitimately remain empty — `TERMINAL` on a system with no x-terminal-emulator alternative, or `OBSIDIAN_VAULT_PATH` if `detect_obsidian_vault()` fails and returns `""`. A blanket "no empty values" guard would reject valid configs where empty means "not applicable" or "auto-detect failed."

**Mitigation:** Define which keys are required (e.g., `USERNAME`, `USER_UID`, `USER_GID`) and which are optional (e.g., `TERMINAL`, `OBSIDIAN_VAULT_PATH`). Only enforce non-empty on required keys. Document this distinction in the plan.

---

## Conflicts

### 4. Nouveau GSP grub config deployed by both hardware.sh and Makefile.lockdown

**Status:** Resolved

**Category:** conflict

`hardware.sh` (Step 7) copies `lockdown/grub.d/99-nouveau-gsp.cfg` to `/etc/default/grub.d/` and runs `update-grub`. `Makefile.lockdown` line 44-45 does the same: `cp lockdown/grub.d/* /etc/default/grub.d/`. Both execute in the same orchestrator run (hardware.sh first, then `make -f Makefile.lockdown`). The second copy is redundant but harmless — except the orchestrator marks it as needing reboot in hardware.sh while Makefile.lockdown doesn't. This duplication makes it unclear which component "owns" this config.

**Mitigation:** Decide on a single owner. Since Makefile.lockdown already handles grub.d deployment, remove the nouveau GSP logic from `hardware.sh` entirely and let Makefile.lockdown own it. Alternatively, if hardware.sh should own it for idempotency checks, remove the grub.d lines from Makefile.lockdown.

### 5. Makefile.lockdown requires sudo but plan shows bare make invocation

**Status:** Resolved

**Category:** conflict

Makefile.lockdown line 1 states `# requires: sudo make -f Makefile.lockdown lockdown`. The Makefile copies files to `/etc/`, `/opt/`, `/usr/local/` — all root-owned paths. The plan shows `step "lockdown" make -f Makefile.lockdown ...` without `sudo`. Even with sudo keepalive, `make` itself is not called with sudo and Makefile targets don't automatically escalate. The Makefile.lockdown relies on being invoked as root.

**Mitigation:** The orchestrator must call `sudo make -f Makefile.lockdown lockdown`. The plan should explicitly show `sudo` in the step call.

### 6. github_setup.sh depends on gh being installed — no dependency validation

**Status:** Resolved

**Category:** conflict

`github_setup.sh` runs `gh auth login --with-token` which requires the `gh` CLI binary. `gh` is installed by `packages.sh` (in the apt package list). If packages.sh fails to install `gh` (apt failure, network issue), github_setup.sh will fail with a confusing "command not found" error. Similarly, `go_installs.txt` requires `go` (installed via apt), and `cargo_builds.txt` requires `cargo` (installed via rustup within the module itself). The plan relies on execution order but doesn't validate prerequisites.

**Mitigation:** Add a prerequisite check at the start of each module: `cmd_exists gh || { log_error "gh not found — install packages first"; exit 1; }`. This gives clear error messages instead of cryptic failures. The plan should mention this pattern.

---

## Edge Cases

### 7. Log directory ~/.config/install/ never created

**Status:** Resolved

**Category:** edge-case

`LOG_FILE` is defined as `~/.config/install/install.$(date +%F).log` with "created on first log call." But `~/.config/install/` doesn't exist on a fresh system. If `log()` tries to write to this path without `mkdir -p`, it will fail with "No such file or directory."

**Mitigation:** `common.sh` must run `mkdir -p "$(dirname "$LOG_FILE")"` during initialization (when the variable is set), before any log calls. The plan should specify this in Step 2.

### 8. gnupg and curl bootstrap install not accounted for

**Status:** Resolved

**Category:** edge-case

The old install.sh runs `sudo apt install -y gnupg curl` (line 108) before any package operations. This is required for Brave/Chrome GPG key import (`gpg --dearmor`) and for all `curl` downloads. If packages.sh reads `apt_repos.txt` first and tries to import GPG keys, `gpg` won't be available. If it tries GitHub API calls, `curl` won't be available. The plan's `apt.txt` package list doesn't explicitly include `gnupg` or `curl`.

**Mitigation:** `packages.sh` should install `gnupg curl` as a hardcoded first step before processing any inventory files, or add them to `apt.txt` and ensure `install_apt_list()` runs before all other package functions. The plan should document this bootstrap ordering.

### 9. Module subprocess output bypasses log file

**Status:** Resolved

**Category:** edge-case

`common.sh` provides `log()` which writes to both stdout and `LOG_FILE`. But modules running as subprocesses have their own stdout/stderr going to the terminal. Any `echo` or raw output from a module (not using `log()`) won't appear in the log file. The plan says "every action is recorded — not just errors" but this depends entirely on every module consistently using `log()` rather than `echo`. Additionally, the orchestrator doesn't capture or redirect module subprocess output to the log file.

**Mitigation:** The `step()` wrapper should redirect module subprocess stdout/stderr to the log file (e.g., `bash lib/module.sh >> "$LOG_FILE" 2>&1`) while also printing a summary line. Or require that all modules exclusively use `log()` and never raw `echo`. The plan should pick one approach and specify it.

### 10. apt update not called before apt install

**Status:** Resolved

**Category:** edge-case

The old install.sh calls `sudo apt install -y` without a preceding `apt update` for the main package batch (lines 105-141). It only calls `apt update` before Brave (line 186) and Chrome (line 203). On a fresh Debian install, the apt cache may be stale or empty. `apt install` without `apt update` can fail with "unable to locate package" even for packages in configured repositories.

**Mitigation:** `packages.sh` should run `sudo apt update` once before processing `apt.txt` and `apt_repos.txt`. Add it as the first action in `install_apt_list()` or as a separate `refresh_apt_cache()` function called before all apt operations.

### 11. GITHUB_TOKEN validity not checked

**Status:** Resolved

**Category:** edge-case

The plan says "GitHub API calls use GITHUB_TOKEN from env for authenticated requests. If token is empty, falls back to unauthenticated." But a token can be non-empty and still invalid (expired, revoked, wrong scopes). An invalid token returns HTTP 401, which the `grep "browser_download_url"` parser would silently produce an empty URL, leading to a confusing "Could not determine URL" warning.

**Mitigation:** credentials.sh should validate the token after prompting (e.g., `curl -sf -H "Authorization: token $GITHUB_TOKEN" https://api.github.com/user`) and warn if it's invalid. This prevents downstream modules from silently falling back to unauthenticated with degraded rate limits.

### 12. Seal credential directory creation dropped without replacement

**Status:** Resolved

**Category:** edge-case

The old install.sh creates `~/.config/seal/` and touch/chmod's `system.credentials` and `mobile.credentials` (lines 423-425). No planned module handles this. The plan's scope says "Changes to lockdown/" are out of scope, but the seal credential directory is a user-level config dir (`~/.config/seal/`), not part of the lockdown directory. The seal/unseal system (`unseal`, `sem`) depends on these files existing. If they're not created, manual_work.md steps 3-3a would fail.

**Mitigation:** Either add seal credential dir creation to `system_config.sh` or create a dedicated `lib/seal_init.sh` module. The plan's mapping table should account for old install.sh lines 423-425.

---

## Ambiguities

### 13. Pipe-delimited format shows spaces around pipes

**Status:** Resolved

**Category:** ambiguity

The plan shows format as `name | check_cmd | key_url | keyring | repo_line | repo_file` with spaces around pipes. `IFS='|' read -r name check_cmd key_url keyring repo_line repo_file` would assign `" name "` (with spaces) to `name`, `" check_cmd "` to `check_cmd`, etc. A `command -v " brave-browser"` would fail because `command -v` doesn't match paths with leading spaces. URLs with spaces would be invalid.

**Mitigation:** Either specify the format without spaces around pipes (`name|check_cmd|key_url|...`), or document that each reader function must trim fields with `xargs` or `${var## }` / `${var%% }` after reading.

### 14. shell field in curl_scripts.txt has undefined semantics

**Status:** Resolved

**Category:** ambiguity

The format is `name | check_cmd | url | shell`. The `shell` field presumably indicates which shell to pipe the curl output into (e.g., `sh` or `bash`). The old install.sh uses `curl ... | sh` for Zed (line 333) and `curl ... | bash` for OpenCode (line 338). The plan doesn't document what values the `shell` field accepts, whether it's validated, or what happens if it's empty. Piping curl output to an empty shell string would cause a confusing error.

**Mitigation:** Document valid values for the `shell` field (e.g., `sh`, `bash`). Add validation in `install_curl_scripts()` that rejects empty or unrecognized shell values. Consider using `sh -c` as the safe default.

### 15. enable_services() ownership split with Makefile.lockdown unclear

**Status:** Resolved

**Category:** ambiguity

The old install.sh enables NetworkManager and nftables (lines 143-147). The plan puts this in `packages.sh → enable_services()`, but `system_config.sh` handles DNS (which depends on NetworkManager being enabled) and nftables base config (which is handled by Makefile.lockdown). `enable_services()` enabling nftables in packages.sh would conflict with Makefile.lockdown also configuring nftables. The execution order is: packages.sh enables nftables, then later Makefile.lockdown overwrites `/etc/nftables.conf`.

**Mitigation:** `enable_services()` should only enable services (`systemctl enable --now`). The nftables configuration (`nftables.conf` content) should be exclusively owned by Makefile.lockdown. Document this ownership split clearly.

---

## Completeness Gaps

### 16. Utility script deployment dropped without explanation

**Status:** Resolved

**Category:** completeness-gap

The old install.sh lines 429-461 deploy utility scripts (`unseal.py`, `sem.py`, `seal_lib.py`, `enter-internet-netns`, `setup-internet-netns.sh`) from `.config/scripts/` and `.config/allowlist/scripts/` to `/usr/local/bin/` and `/usr/local/lib/`. The plan maps these to "Makefile + Makefile.lockdown" (lines 404-479). `Makefile.lockdown` handles some scripts (lines 46-50), but the old install.sh also deploys `seal_lib.py` to `/usr/local/bin/` (line 454), creates `.config/scripts/seal_lib.py` symlink (lines 416-422), and deploys scripts from `.config/scripts/*.sh` (lines 430-440). The `.config/` paths are broken (the plan correctly identifies this), but the deployment functionality is lost entirely.

**Mitigation:** Audit whether `seal_lib.py` deployment to `/usr/local/bin/` is still needed by the seal system. If yes, add it to `Makefile.lockdown` or a new module. If not, explicitly document why it's dropped. The plan should have a row in the mapping table for lines 416-461 with an explicit "dropped — reason" note rather than lumping them into "Makefile + Makefile.lockdown."

### 17. source ~/.config/bashrc side-effect has no equivalent

**Status:** Resolved

**Category:** completeness-gap

The old install.sh runs `source ~/.config/bashrc` (line 271) during installation. This loads shell functions/aliases into the installer's environment, which may be used by subsequent steps. No planned module replicates this. The Makefile handles bashrc deployment (`cp dotfiles/bashrc $(HOME)/.bashrc`), but the sourcing during install is a separate concern. If any downstream step depends on aliases or functions defined in bashrc, this gap could cause failures.

**Mitigation:** Determine if any install steps depend on bashrc-sourced functions. If not, explicitly note that the `source ~/.config/bashrc` line is intentionally dropped (it was likely unnecessary). If yes, add it to the appropriate module or orchestrator.

### 18. No .gitignore update for new state files

**Status:** Resolved

**Category:** completeness-gap

The plan creates `lib/` and `packages/` directories with shell scripts and text files that should be tracked by git. The existing `.gitignore` has `*.env` which would not affect these directories, but should be verified. More importantly, the `.install-need-reboot` state file (if using the mitigation from the NEEDS_REBOOT finding) should be gitignored.

**Mitigation:** Update `.gitignore` to include any local state files the orchestrator creates (e.g., `.install-need-reboot`). Verify `lib/` and `packages/` aren't accidentally excluded.

### 19. Reboot prompt may not execute if orchestrator crashes

**Status:** Resolved

**Category:** completeness-gap

The orchestrator's EXIT trap runs `kill $KEEPALIVE_PID` and `sudo -k`. messages.sh prompts for reboot using `read -p "Reboot now? (y/N):"` and then calls `sudo systemctl reboot`. If messages.sh runs after the trap fires (e.g., due to an error in the orchestrator flow before messages.sh), the reboot prompt's `sudo systemctl reboot` would fail. The plan says messages.sh "always runs" but if any earlier step crashes the orchestrator, the trap fires and messages.sh never runs.

**Mitigation:** Place the reboot prompt as part of the orchestrator's main flow (not a module) and run it after the summary but before exit. The trap should only kill the keepalive loop, not revoke sudo. Alternatively, move `sudo -k` out of the trap entirely.

### 20. cargo build has no timeout or retry strategy

**Status:** Resolved

**Category:** completeness-gap

The plan says `retry 3` is for "network-dependent operations (GitHub API, downloads)." But `cargo build` makes hundreds of requests to crates.io, which can be slow or fail. `cargo build` doesn't have a simple retry mechanism — if it fails partway, re-running is generally safe (cargo caches), but the plan doesn't address this. Building UAD-NG from source can take 5-15 minutes with no timeout specified.

**Mitigation:** Wrap `cargo build` in the `retry` helper (cargo is idempotent — re-running picks up where it left off). Add a timeout (e.g., `timeout 600 cargo build`). Document expected build time.
