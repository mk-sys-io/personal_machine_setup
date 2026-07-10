# Phase 1 — Dotfiles + Dev restructure

## Problem

The repo has two structural problems that block further work:

1. **`.config/` is a flat mix of 3 layers** — 25 entries mixing dotfiles (sway, waybar, foot), dev tools (github.env, opencode), and lockdown system (allowlist, nftables, sudoers) in one directory with no grouping.

2. **Hardcoded values scattered everywhere** — username `mike`, `/home/mike/` paths, UID `1000` hardcoded in 17+ locations across bashrc, scripts, and Makefile. Changing any of these requires editing multiple files.

Phase 1 addresses only dotfiles and dev. Lockdown stays in `.config/` until Phase 2 (it requires sudo for root-owned files and needs separate care).

---

## Scope

### What Phase 1 covers

- **Dotfiles** — `.config/{bashrc,brave,copyq,firefox,foot,fuzzel,fzf,obsidian,ranger,sway,waybar,zed}/` → `dotfiles/`
- **Dev** — `.config/{github.env,github.env.template,opencode,container}/` → `dev/`
- **Cross-layer config** — (does not exist) → `config.env` at repo root
- **Bootstrap tool** — (does not exist) → `tools/bootstrap-config.sh`

### What Phase 1 does NOT cover

- Lockdown system (`allowlist/`, `nftables/`, `polkit/`, `sudoers/`, `systemd/`, `resolv/`, `scripts/`, `grub.d/`, `sysctl.d/`) — stays in `.config/` until Phase 2
- Theme system (extraction from theme.env, quality checks) — deferred
- Import tools for external repos — deferred
- install.sh factoring — deferred

---

## Execution

### Step 1 — Create directory structure

```
dotfiles/
├── bashrc            (← .config/bashrc)
├── brave/            (← .config/brave/)
├── copyq/            (← .config/copyq/)
├── firefox/          (← .config/firefox/)
├── foot/             (← .config/foot/)
├── fuzzel/           (← .config/fuzzel/)
├── fzf/              (← .config/fzf/)
├── obsidian/         (← .config/obsidian/)
├── ranger/           (← .config/ranger/)
├── sway/             (← .config/sway/)
├── waybar/           (← .config/waybar/)
├── zed/              (← .config/zed/)
├── themes/           (empty, ready for future)
└── background.jpg    (new placeholder — see issue #8)

dev/
├── github.env            (← .config/github.env)
├── github.env.template   (← .config/github.env.template)
├── opencode/             (← .config/opencode/)
└── container/            (← .config/container/)

tools/
└── bootstrap-config.sh   (NEW)

lockdown/                 (empty — Phase 2)
```

### Step 2 — `tools/bootstrap-config.sh`

Auto-detects system values and proposes `config.env`:

- `USERNAME` — `whoami`
- `USER_UID` — `id -u`
- `USER_GID` — `id -g`
- `OPENCODE_PATH` — check `~/.opencode/bin/opencode` exists → that dir. Otherwise suggest `~/.opencode`
- `OBSIDIAN_VAULT_PATH` — check `~/knowledge_base`, `~/Obsidian`, `~/Documents/Obsidian` in order — first hit wins, else prompt
- `TERMINAL` — `update-alternatives --list x-terminal-emulator 2>/dev/null | head -1`

Behavior:
- Prints detected + proposed values
- Opens `$EDITOR` (or auto-detected: zed → nano → vim → vi) with proposed content if user chooses edit
- Writes `config.env` directly on confirmation
- Stores at repo root as `config.env`

### Step 3 — `config.env` (repo root)

```bash
# ── Identity ──
USERNAME=mike
USER_UID=1000
USER_GID=1000

# ── Paths ──
OPENCODE_PATH=/home/mike/.opencode
OBSIDIAN_VAULT_PATH=/home/mike/knowledge_base

# ── Default app ──
TERMINAL=foot
```

Kept minimal — only values that cross layer boundaries. Network/seal paths stay in lockdown/ until Phase 2.

**Note:** `config.env` should be added to `.gitignore` — it contains machine-specific values (username, paths) that shouldn't be committed.

### Step 4 — `dotfiles/bashrc`

Templated version of the existing `.config/bashrc` with `@VARIABLE@` placeholders:

```bash
# --- linux_setup additions ---

alias docker="podman"
alias reboot='sudo systemctl reboot'
alias poweroff='sudo systemctl poweroff'
alias suspend='sudo systemctl suspend'
alias rm='rm -I'

# Tab completion: cycle through matches inline (no list break)
[[ $- == *i* ]] && bind 'TAB:menu-complete'

if [ -z "${DISPLAY:-}" ] && [ -z "${WAYLAND_DISPLAY:-}" ] && [ "${XDG_VTNR:-}" -eq 1 ]; then
    exec sway
fi

# opencode
# export PATH=@OPENCODE_PATH@/bin:$PATH
# zed
export PATH=$HOME/.local/bin:$PATH
# go
export PATH=$HOME/go/bin:$PATH

# fzf — key bindings and completion
[ -f /usr/share/doc/fzf/examples/key-bindings.bash ] && source /usr/share/doc/fzf/examples/key-bindings.bash
[ -f /usr/share/doc/fzf/examples/completion.bash ] && source /usr/share/doc/fzf/examples/completion.bash
# fzf — custom env (fd backend, dotfiles hidden)
[ -f ~/.config/fzf/env.sh ] && source ~/.config/fzf/env.sh

# Internet network namespace aliases
# Naming: bare name for unrestricted binaries, binary-subcommand for restricted
alias opencode='sudo enter-internet-netns @OPENCODE_PATH@/bin/opencode'
alias podman-pull='sudo enter-internet-netns /usr/bin/podman pull'
```

Design notes:
- `@OPENCODE_PATH@` placeholder is substituted by Makefile during deployment
- Only the opencode-related paths use templating — everything else stays static
- The commented-out `PATH` line is preserved as-is (already commented in the original)

### Step 5 — Copy dotfiles from `.config/` → `dotfiles/`

Most files are static copies (no templating). Exceptions:

- `dotfiles/bashrc` — `@OPENCODE_PATH@`
- `dotfiles/obsidian/appearance.json` — none (path is in Makefile, not the file itself)

All other dotfiles (foot, fuzzel, sway, waybar, copyq, ranger, fzf, brave, firefox, zed) are copied as-is.

### Step 6 — Copy dev from `.config/` → `dev/`

Static copies, no templating:
- `github.env` → `dev/github.env`
- `github.env.template` → `dev/github.env.template`
- `opencode/` → `dev/opencode/`
- `container/` → `dev/container/`

### Step 7 — Rewrite `Makefile`

Replace the old `make config` target with:

```makefile
include config.env

DEPLOY_DIR := $(HOME)/.config
SUBST := sed -i 's/@USERNAME@/$(USERNAME)/g; s/@OPENCODE_PATH@/$(OPENCODE_PATH)/g; s/@OBSIDIAN_VAULT_PATH@/$(OBSIDIAN_VAULT_PATH)/g'

.PHONY: dotfiles dev all

dotfiles:
	@echo "=== Dotfiles ==="
	# bashrc
	cp dotfiles/bashrc $(HOME)/.bashrc
	$(SUBST) $(HOME)/.bashrc
	# app config dirs
	for app in foot fuzzel sway waybar copyq ranger fzf obsidian zed; do \
		mkdir -p $(DEPLOY_DIR)/$$app; \
		cp -r dotfiles/$$app/* $(DEPLOY_DIR)/$$app/; \
	done
	# brave/firefox (policy dirs)
	cp -r dotfiles/brave/*   $(DEPLOY_DIR)/brave/
	cp -r dotfiles/firefox/* $(DEPLOY_DIR)/firefox/
	# copyq theme subdir
	mkdir -p $(DEPLOY_DIR)/copyq/themes
	cp -r dotfiles/copyq/themes/* $(DEPLOY_DIR)/copyq/themes/
	# waybar scripts subdir
	mkdir -p $(DEPLOY_DIR)/waybar/scripts
	cp -r dotfiles/waybar/scripts/* $(DEPLOY_DIR)/waybar/scripts/
	# obsidian (custom vault path)
	mkdir -p $(OBSIDIAN_VAULT_PATH)/.obsidian
	cp dotfiles/obsidian/* $(OBSIDIAN_VAULT_PATH)/.obsidian/
	@echo "Dotfiles deployed."

dev:
	@echo "=== Dev ==="
	mkdir -p $(DEPLOY_DIR)/opencode $(DEPLOY_DIR)/container
	cp dev/github.env       $(DEPLOY_DIR)/github.env
	cp dev/opencode/*       $(DEPLOY_DIR)/opencode/
	cp dev/container/*      $(DEPLOY_DIR)/container/ 2>/dev/null || true
	@echo "Dev configs deployed."

all: dotfiles dev
```

### Step 8 — Test

1. Run `make dotfiles` — verify files land in `~/.config/<app>/` and `~/.bashrc`
2. Source `~/.bashrc` in current shell — verify aliases, fzf, prompt work
3. Run `make dev` — verify `~/.config/github.env` is present
4. Verify `.config/` is untouched (archive reference still valid)

### Step 9 — Archive `.config/`

After confirming everything works:
- Rename `.config/` → `archive/old-config/`
- Add `archive/old-config/README.md` noting it's a frozen reference
- Update `.gitignore` if needed

---

## Files changed / created

**Create**
- `rework/phase1-plan.md`
- `tools/bootstrap-config.sh`
- `config.env`
- `dotfiles/` (directory)
- `dotfiles/bashrc`
- `dotfiles/themes/` (empty)
- `dotfiles/background.jpg` (placeholder)
- `dev/` (directory)
- `lockdown/` (empty directory)

**Copy from `.config/`**
- `dotfiles/brave/`
- `dotfiles/copyq/`
- `dotfiles/firefox/`
- `dotfiles/foot/`
- `dotfiles/fuzzel/`
- `dotfiles/fzf/`
- `dotfiles/obsidian/`
- `dotfiles/ranger/`
- `dotfiles/sway/`
- `dotfiles/waybar/`
- `dotfiles/zed/`
- `dev/github.env`
- `dev/github.env.template`
- `dev/opencode/`
- `dev/container/`

**Rewrite**
- `Makefile`

**Skip**
- `.config/github.env` in git tracking (already in .gitignore)
