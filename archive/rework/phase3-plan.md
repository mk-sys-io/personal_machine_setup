# Phase 3 — install.sh restructure

## Problem

`install.sh` is a 661-line monolithic bash script with three structural problems:

1. **Broken paths** — References `.config/` paths that no longer exist after Phase 1 (moved to `dotfiles/`, `lockdown/`, `dev/`).
2. **No modularity** — Cannot run individual steps. Adding anything means editing this one fragile script.
3. **No error handling** — `set -euo pipefail` crashes on first error with no log, no partial-success summary, no way to resume.

Phase 1 and Phase 2 moved dotfiles/dev to `Makefile` and lockdown to `Makefile.lockdown`. What remains in `install.sh` is: pre-flight checks, package installation, hardware config, system config, tool installation, and GitHub setup. These are now orphaned from the new architecture.

---

## Scope

### What Phase 3 covers

- **Rewrite** `install.sh` as a modular orchestrator calling independent shell modules
- **Create** `lib/` directory with independent install modules (shell scripts)
- **Create** `packages/` directory with declarative package inventories (plain text)
- **Archive** old `install.sh` to `archive/old-install.sh`
- **Modify** `Makefile.lockdown` — remove grub.d deployment, add policy template deployment

### What Phase 3 does NOT cover

- Changes to `Makefile`, `dotfiles/`, `lockdown/`, `dev/`
- Theme system improvements
- Import tools for external repos

---

## Design Decisions

| Decision | Approach | Rationale |
|----------|----------|-----------|
| All modules | Shell (`#!/usr/bin/env bash`) | Every operation is a native shell capability. No Python dependency. |
| Orchestrator | Shell (`install.sh`) | Bash `step()` function with results array — proven pattern in `check-firmware.sh` and `verify.sh` |
| Package inventory | Plain text, pipe-delimited | Parseable with `IFS='\|' read -r` in shell. No JSON, no jq. Editable without touching code. |
| Error handling | `step()` wrapper per module, non-fatal by default | Modules exit 0 (pass), exit 1 (fail), exit 2 (skip), exit 3 (partial success). Orchestrator captures codes, continues, summary at end. |
| Orchestrator `set -e` | **No `set -e` in orchestrator** | Orchestrator uses `if bash lib/module.sh; then` to capture exit codes. Modules keep `set -euo pipefail` internally. |
| Orchestrator `set -u` | **Not used** | Orchestrator is ~200 lines with simple flow. Typos are low risk. Add later if needed. |
| Orchestrator `set -a` | **Used** | Modules need config.env variables (`GITHUB_TOKEN`, `USERNAME`, etc.) and sudo propagation. Internal orchestrator vars leaking is benign. |
| Retries | `retry N CMD...` helper in `lib/common.sh` | Network-dependent operations retry up to 3 times with 3s delay. Inspired by `seal_lib.py` pattern but generalized. |
| Sudo management | Orchestrator acquires + keepalive loop, modules inherit | Same pattern as current install.sh. Modules call `sudo` normally. |
| Resume | Skip-if-exists idempotency in each module | No state file. Each module checks if work is already done before acting. |
| Credentials | **Automated — require `dev/github.env` pre-filled** | No interactive prompts. Orchestrator validates file exists and tokens are non-empty. User creates via `cp dev/github.env.template dev/github.env` and fills in values before running install.sh. |
| Module ordering | Numeric prefixes (`00-`, `20-`, `30-`) | Execution order encoded in filenames. No hardcoded list in orchestrator. |
| Module discovery | **Hardcoded ordered list** | Orchestrator runs modules in explicit order. Adding a new module = adding to the list + creating the file. Simple, predictable. |
| CLI flags | **None** | Linear flow. For re-running individual parts, document manual commands (Makefiles, standalone modules). |
| Prerequisite checks | Each module validates its own deps | Modules check `cmd_exists` / `pkg_installed` at startup. Fails with clear message if missing. |
| NEEDS_REBOOT | File-based signal (`~/.config/install/.install-need-reboot`) | Orchestrator **warns if marker exists at startup** (previous reboot not completed), then deletes. `hardware.sh` recreates if condition persists. |

---

## Architecture

### Orchestrator flow

```
install.sh
  1. Warn if ~/.config/install/.install-need-reboot exists (previous reboot pending)
  2. Delete ~/.config/install/.install-need-reboot (clean slate for this run)
  3. Validate config.env (exit if missing/empty required keys)
  4. Validate dev/github.env (exit if missing, warn if GITHUB_TOKEN empty)
  5. Source config.env (set -a → export all to children, set +a)
  6. Source dev/github.env (export GITHUB_TOKEN, GIT_USER_NAME, GIT_USER_EMAIL)
  7. Acquire sudo + start keepalive loop
  8. Initialize log file at ~/.config/install/install.<date>.log
  9. Run modules in order via step():
     - lib/00-checks.sh       (pre-flight)
     - lib/20-packages.sh     (apt, repos, github debs, binaries, go, cargo, curl scripts)
     - lib/30-hardware.sh     (backlight, wifi power, nouveau GSP)
     - lib/40-system_config.sh (dns, podman dns, dark mode, seal dirs)
     - lib/50-github_setup.sh  (gh auth, git config)
  10. Run make -C "$REPO_ROOT" all (dotfiles + dev)
  11. Run sudo make -f Makefile.lockdown lockdown
  12. Print summary, prompt reboot, cleanup
```

### Module execution model

Each module is a standalone shell script. The orchestrator runs each via `bash lib/module.sh >> "$LOG_FILE" 2>&1`, capturing the exit code. Module subprocess stdout/stderr is appended to the log file. The terminal only sees the step summary line. All non-checks steps are non-fatal — failure in one module does not abort the rest.

Exit codes:
- `0` = pass (success or already configured)
- `1` = failure
- `2` = skip (not applicable, e.g., no backlight device)
- `3` = partial success (some items installed, some failed)

Each module validates its own prerequisites at startup. If a required tool is missing, the module exits 1 with a clear message.

Modules — explicit ordered list:
```
lib/00-checks.sh          # pre-flight (network, permissions)
lib/20-packages.sh        # apt, repos, github debs, binaries, go, cargo, curl scripts
lib/30-hardware.sh        # backlight, wifi power, nouveau GSP
lib/40-system_config.sh   # dns, podman dns, dark mode, seal dirs
lib/50-github_setup.sh    # gh auth, git config
```

To add a new module: create `lib/XX-name.sh` and add it to the orchestrator's module list.

### Re-run commands (documented in README)

| Scenario | Command |
|----------|---------|
| Changed a dotfile config | `make -C "$REPO_ROOT" all` |
| Added a new apt package | Add line to `packages/apt.txt`, run `bash lib/20-packages.sh` |
| Added a new GitHub .deb | Add line to `packages/github_deb.txt`, run `bash lib/20-packages.sh` |
| Changed lockdown config | `sudo make -f Makefile.lockdown lockdown` |
| Changed hardware config | `bash lib/30-hardware.sh` |
| Changed system config | `bash lib/40-system_config.sh` |

### Sudo propagation

Same as current install.sh. Orchestrator calls `sudo -v` once at startup. Background loop refreshes every 60 seconds. `trap` on EXIT kills the loop. `sudo -k` runs explicitly after reboot prompt.

### config.env guard

Before any module runs, the orchestrator validates `config.env`:

1. File must exist (exit with error pointing to `tools/bootstrap-config.sh`)
2. Required keys must be non-empty (exit with error naming the empty key):
   - `USERNAME`, `USER_UID`, `USER_GID`
3. Optional keys allowed empty — modules skip gracefully:
   - `TERMINAL`, `OBSIDIAN_VAULT_PATH`, `OPENCODE_PATH`, `DEFAULT_EDITOR`

### Credentials guard

Before any module runs, the orchestrator validates `dev/github.env`:

1. File must exist (exit with error: `cp dev/github.env.template dev/github.env`)
2. Source the file
3. If `GITHUB_TOKEN` is empty, warn: "GitHub API calls will be unauthenticated (60 req/h limit)"
4. If `GIT_USER_NAME` or `GIT_USER_EMAIL` is empty, warn: "Git commits will have no author info"

No interactive prompts. All automation.

### Shared helpers

Each module sources `lib/common.sh`:
- `REPO_ROOT` — resolved path to repo root
- `LOG_DIR` — `~/.config/install/`
- `LOG_FILE` — `~/.config/install/install.$(date +%F).log` (truncated at init time, each run gets a fresh log)
- `NEEDS_REBOOT_FILE` — `~/.config/install/.install-need-reboot`
- `log()`, `log_ok()`, `log_warn()`, `log_error()` — timestamped output to stdout + log file
- `retry N CMD...` — tries CMD up to N times with 3s delay
- `cmd_exists()` — `command -v` wrapper
- `pkg_installed()` — `dpkg -s` wrapper
- `needs_reboot()` — creates `$NEEDS_REBOOT_FILE`

---

## Execution

### Step 1 — Create directory structure

```
linux_setup/
├── install.sh              (REWRITTEN — bash orchestrator)
├── lib/                    (NEW — install modules)
│   ├── common.sh
│   ├── 00-checks.sh
│   ├── 20-packages.sh
│   ├── 30-hardware.sh
│   ├── 40-system_config.sh
│   └── 50-github_setup.sh
├── packages/               (NEW — declarative inventories)
│   ├── apt.txt
│   ├── apt_repos.txt
│   ├── github_deb.txt
│   ├── github_binary.txt
│   ├── go_installs.txt
│   ├── cargo_builds.txt
│   └── curl_scripts.txt
```

### Step 2 — Create `lib/common.sh`

Shared helpers sourced by all modules (as listed above).

### Step 3 — Create `lib/00-checks.sh`

Pre-flight checks module. No prerequisites.
- Root user check (`$EUID -eq 0`)
- Sudo group check (`groups | grep '\bsudo\b'`)
- DNS resolution check (`getent hosts raw.githubusercontent.com`)
- TCP connectivity check (`echo > /dev/tcp/raw.githubusercontent.com/443`)
- Exit 1 on failure

### Step 4 — Create `packages/` inventory files

Seven plain-text files, pipe-delimited format.

**`packages/apt.txt`** — One package per line, `#` comments. ~35 packages.

**`packages/apt_repos.txt`** — `name|check_cmd|key_url|keyring|repo_line|repo_file`. Two entries: Brave, Chrome.

**`packages/github_deb.txt`** — `name|repo|pattern|deps|version`. Two entries: LocalSend, Obsidian.

**`packages/github_binary.txt`** — `name|repo|pattern|dest|version`. One entry: yt-dlp.

**`packages/go_installs.txt`** — `name|import_path|version`. One entry: tle.

**`packages/cargo_builds.txt`** — `name|repo|bin|version`. One entry: UAD-NG.

**`packages/curl_scripts.txt`** — `name|check_cmd|url|shell`. Shell must be `sh` or `bash`. Two entries: Zed, OpenCode.

### Step 5 — Create `lib/20-packages.sh`

Multi-method package installer. Prerequisites: `curl`, `gnupg` (installed as hardcoded first step).

One function per method:
- `install_apt_list()` — apt update once, check dpkg per package, install only missing
- `install_apt_repos()` — check command -v, add repo + GPG key if missing
- `install_github_debs()` — curl API, download .deb, install with dpkg
- `install_github_binaries()` — curl API, copy binary to dest
- `install_go_installs()` — check go/bin/<name>, run go install
- `install_cargo_builds()` — check command -v, install rustup if needed, build with retry
- `install_curl_scripts()` — validate shell field (sh/bash), validate url non-empty, download to temp file with `curl -fsSL -o /tmp/install_<name>.sh "$url"`, then `bash /tmp/install_<name>.sh` (or `sh`). Catches 404s before piping to shell.
- `enable_services()` — systemctl enable --now NetworkManager, nftables

Each function idempotent. Tracks installed/failed counts. Exits 3 if partial success.

### Step 6 — Create `lib/30-hardware.sh`

Hardware configuration module. No prerequisites.
- `setup_backlight()` — detect /sys/class/backlight/*, enable systemd-backlight@ service
- `setup_wifi_power()` — iwlwifi modprobe options + NM power-save config
- `setup_nouveau_gsp()` — check GRUB param, copy lockdown/grub.d/99-nouveau-gsp.cfg, update-grub, needs_reboot

### Step 7 — Create `lib/40-system_config.sh`

System configuration module. No prerequisites.
- `setup_dns()` — NM dns=none, resolv.conf → 127.0.0.1, immutable
- `setup_podman_dns()` — /etc/containers/containers.conf with DNS 1.1.1.1
- `setup_dark_mode()` — gsettings set prefer-dark
- `setup_seal_dirs()` — create ~/.config/seal/, touch credentials files, chmod 600

### Step 8 — Create `lib/50-github_setup.sh`

GitHub CLI + git config. Prerequisite: `gh` CLI.
- `gh auth login --with-token` from GITHUB_TOKEN
- `git config --global credential.helper`
- `git config --global user.name` and `user.email`

### Step 9 — Post-install summary, reboot prompt, cleanup (in orchestrator)

No separate module. Orchestrator handles directly:
- Colored summary table (pass/fail/skip/partial — 4 categories per module)
- Log file path reference
- Manual steps reminder (`glow manual_work.md`)
- Reboot prompt if `~/.config/install/.install-need-reboot` exists. Deletes after prompting.
- `sudo -k` after reboot prompt

### Step 10 — Rewrite `install.sh` orchestrator

Bash script (~200 lines):
- No `set -e`, no `set -u`, but uses `set -a` for config.env export
- Warn if reboot marker exists at startup, then delete
- config.env validation (required keys non-empty)
- dev/github.env validation (must exist, warn if token empty)
- Source both config files with `set -a`
- Sudo acquisition + keepalive loop + trap cleanup
- Initialize log file
- Run modules in explicit order via `step()` function
- Run `make -C "$REPO_ROOT" all`
- Run `sudo make -f Makefile.lockdown lockdown`
- Print summary, prompt reboot, cleanup

### Step 11 — Archive old install.sh

- Move current `install.sh` → `archive/old-install.sh`
- Write new `install.sh` as orchestrator

### Step 12 — Modify `Makefile.lockdown`

Remove grub.d deployment (hardware.sh owns nouveau GSP). Add policy template deployment:

**Remove:**
- Line 44: `mkdir -p /etc/default/grub.d/`
- Line 45: `cp lockdown/grub.d/* /etc/default/grub.d/`
- `/etc/default/grub.d/*` from SUBST line 64
- Line 82: `chmod 644 /etc/default/grub.d/*`
- Line 83: `chown root:root /etc/default/grub.d/*`

**Add:**
- `cp lockdown/allowlist/scripts/seal_lib.py /usr/local/bin/seal_lib.py`
- `cp dotfiles/brave/policy.json.template $(ALLOWLIST_PATH)/brave-policy.json.template`
- `cp dotfiles/firefox/policies.json.template $(ALLOWLIST_PATH)/firefox-policies.json.template`
- `sudo $(ALLOWLIST_PATH)/scripts/generate-policies.sh` (deploy browser policies from templates)

---

## Files changed / created

**Create**
- `lib/common.sh`
- `lib/00-checks.sh`
- `lib/20-packages.sh`
- `lib/30-hardware.sh`
- `lib/40-system_config.sh`
- `lib/50-github_setup.sh`
- `packages/apt.txt`
- `packages/apt_repos.txt`
- `packages/github_deb.txt`
- `packages/github_binary.txt`
- `packages/go_installs.txt`
- `packages/cargo_builds.txt`
- `packages/curl_scripts.txt`

**Rewrite**
- `install.sh` (orchestrator)

**Modify**
- `Makefile.lockdown` (remove grub.d, add policy templates + seal_lib.py)

**Archive**
- `install.sh` → `archive/old-install.sh`

**Delete**
- seal_lib.py symlink (`.config/scripts/seal_lib.py` if it exists)

**NOT modified**
- `Makefile`, `config.env`, `config.env.template`
- `dotfiles/`, `dev/`, `lockdown/`
- `tools/bootstrap-config.sh`, `tools/check-firmware.sh`

---

## Mapping: old install.sh → new modules

| old install.sh Lines | new Module | Notes |
|----------------------|-----------|-------|
| 1-33 | `lib/00-checks.sh` | Pre-flight |
| 35-43 | `install.sh` | Sudo keepalive (orchestrator) |
| 47-103 | `install.sh` | Credentials validation (automated, no prompts) |
| 105-141 | `lib/20-packages.sh` → apt.txt | apt packages |
| 143-147 | `lib/20-packages.sh` → enable_services() | NM + nftables |
| 150-171 | `lib/30-hardware.sh` | Backlight |
| 173-205 | `lib/20-packages.sh` → apt_repos.txt | Brave + Chrome |
| 207-221 | `lib/40-system_config.sh` | DNS |
| 223-235 | `lib/30-hardware.sh` | WiFi power |
| 237-252 | `lib/30-hardware.sh` | Nouveau GSP |
| 254-279 | `Makefile` `dotfiles` target | Already handled |
| 281-319 | `lib/20-packages.sh` → github_deb.txt | LocalSend + Obsidian |
| 332-339 | `lib/20-packages.sh` → curl_scripts.txt | Zed + OpenCode |
| 341-349 | `lib/20-packages.sh` → go_installs.txt | tle |
| 351-367 | `lib/20-packages.sh` → github_binary.txt | yt-dlp |
| 370-390 | `lib/20-packages.sh` → cargo_builds.txt | UAD-NG (+ rustup) |
| 392-402 | `lib/50-github_setup.sh` | gh auth + git config |
| 423-425 | `lib/40-system_config.sh` → setup_seal_dirs() | Seal credential dirs |
| 499-536 | `Makefile.lockdown` | Allowlist + policy templates |
| 538-545 | `Makefile.lockdown` | dnsmasq (already handled) |
| 547-575 | `Makefile.lockdown` | nftables, netns, sysctl (already handled) |
| 577-612 | `Makefile.lockdown` | podman, sudoers, netns service (already handled) |
| 614-661 | `install.sh` (orchestrator) | Summary + reboot prompt |

---

## Testing

1. Run `tools/bootstrap-config.sh` — verify config.env is populated
2. Fill in `dev/github.env` from template
3. Run `./install.sh` — verify all modules execute
4. Verify summary table shows pass for each module
5. Verify log file created at `~/.config/install/install.<date>.log`
6. Re-run `./install.sh` — verify all modules show "already installed" / "already configured"
7. Delete one apt package, re-run — verify only that package is installed
8. Test config.env guard: empty USERNAME → exit with error. Empty TERMINAL → proceeds (optional).
9. Test credentials guard: delete dev/github.env → exit with error pointing to template
10. Test reboot marker: create marker manually, run install.sh → verify warning appears at startup
11. Test standalone module: `bash lib/30-hardware.sh` — verify it works independently
12. Test prerequisite failure: run `bash lib/50-github_setup.sh` without gh → verify clear error
13. Test re-run: `make -C "$REPO_ROOT" all` — verify dotfiles redeployed
14. Test re-run: `sudo make -f Makefile.lockdown lockdown` — verify lockdown redeployed

---

## Adding a new tool after Phase 3

**apt package:** add one line to `packages/apt.txt`
**GitHub .deb:** add one line to `packages/github_deb.txt`
**GitHub binary:** add one line to `packages/github_binary.txt`
**Go tool:** add one line to `packages/go_installs.txt`
**Cargo tool:** add one line to `packages/cargo_builds.txt`
**curl-piped-to-bash:** add one line to `packages/curl_scripts.txt`

Then re-run: `bash lib/20-packages.sh`

No code changes. No editing install logic.

## Adding a new module after Phase 3

1. Create `lib/XX-name.sh` with a numeric prefix
2. Source `common.sh` at the top
3. Add prerequisite checks if needed
4. Implement logic using `log_ok`, `log_warn`, `log_error`
5. Exit with appropriate code: 0 (pass), 1 (fail), 2 (skip), 3 (partial)
6. Add to the orchestrator's module list
7. Done

---

## README updates (after Phase 3)

The README Quick start section should be updated to reflect the new architecture:

### Quick start (revised)

```bash
# 1. Generate config.env (auto-detects system values)
tools/bootstrap-config.sh

# 2. Review and confirm values
nano config.env

# 3. Create GitHub credentials file
cp dev/github.env.template dev/github.env
nano dev/github.env  # fill in GITHUB_TOKEN, GIT_USER_NAME, GIT_USER_EMAIL

# 4. Run full install
./install.sh
```

### Adding a new package

1. Add a line to the appropriate inventory file in `packages/`:
   - `packages/apt.txt` — apt packages (one per line)
   - `packages/github_deb.txt` — GitHub .deb releases (pipe-delimited)
   - `packages/github_binary.txt` — GitHub standalone binaries
   - `packages/go_installs.txt` — Go tools
   - `packages/cargo_builds.txt` — Cargo/Rust tools
   - `packages/curl_scripts.txt` — curl-piped-to-bash scripts
2. Run `bash lib/20-packages.sh` to install

No code changes needed — just a line in a text file.

### Updating dotfiles or dev configs

After changing files in `dotfiles/` or `dev/`:

```bash
make all
```

This re-deploys all dotfiles and dev configs to `~/.config/`.

### Updating lockdown configs

After changing files in `lockdown/`:

```bash
sudo make -f Makefile.lockdown lockdown
```

### Re-running individual modules

Each module is standalone and can be run independently:

```bash
bash lib/30-hardware.sh       # re-run hardware config
bash lib/40-system_config.sh  # re-run system config
bash lib/50-github_setup.sh   # re-run GitHub setup
```
