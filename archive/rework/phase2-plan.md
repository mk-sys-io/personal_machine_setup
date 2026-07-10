# Phase 2 — Lockdown restructure

## Problem

After Phase 1, the lockdown system (allowlist, nftables, polkit, sudoers, resolv, systemd, scripts, grub.d, sysctl.d) lives in `archive/old-config/` — the frozen reference of the old flat directory. It has three issues that Phase 2 addresses:

1. **Not migrated** — lockdown entries are archived alongside dotfiles/dev, not in their own directory
2. **Hardcoded app names** — `seal_lib.py` directly calls `copyq`, `wl-copy`, and scans for `sway`/`waybar`/`copyq` by name. Swapping any of these breaks seal/unseal silently
3. **Hardcoded identity values** — username `mike`, UID `1000`, paths like `/opt/allowlist` are embedded deep in Python and shell scripts

---

## Known-breaking change

**`install.sh` will be broken after Phase 2.** It references `.config/` paths that no longer exist (moved to `archive/old-config/` in Phase 1). After Phase 2, use `Makefile` + `Makefile.lockdown` instead. Factoring `install.sh` is deferred to Phase 3.

---

## Scope

### What Phase 2 covers

- **Move** — lockdown entries from `archive/old-config/` to `lockdown/` + user-facing tools to `tools/`
- **Adapt** — create `lockdown/lib/` abstraction adapters so lockdown never calls a specific app by name
- **Template** — replace hardcoded identity values with `@VARIABLE@` sourced from `config.env`
- **Deploy** — `Makefile.lockdown` (separate file, requires sudo)
- **Expand** — add lockdown-specific variables to `config.env`

### What Phase 2 does NOT cover

- install.sh factoring — deferred to Phase 3
- Theme system improvements — deferred
- Import tools — deferred

---

## Decisions

| Decision | Approach | Rationale |
|---|---|---|
| Separate Makefile | `Makefile.lockdown` for sudo targets | Keeps `make all` user-level, avoids `$(HOME)` resolution issues when running as root |
| Python templating | Strict env lookup: `os.environ["USERNAME"]` | No fallback — fails immediately if config.env isn't sourced. Prevents silent misconfiguration |
| `make all` | Stays user-level, no sudo | `make all` = `dotfiles` + `dev` only. Lockdown is separate invocation |
| Adapters | Installed to `LOCKDOWN_LIB_PATH`, must be in `$PATH` | seal/unseal call adapters by name, not hardcoded tools |

---

## Execution

### Step 0 — Update `config.env` with lockdown vars

Add to the existing `config.env` (repo root):

```bash
# ── Lockdown paths (Phase 2) ──
ALLOWLIST_PATH=/opt/allowlist
LOCKDOWN_LIB_PATH=/usr/local/lib/lockdown
LOCKDOWN_BIN_PATH=/usr/local/bin
DNS_PRIMARY=1.1.1.1
DNS_SECONDARY=2606:4700:4700::1111
NETNS_SUBNET=10.0.4.0/30
NETNS_HOST=10.0.4.1
NETNS_CLIENT=10.0.4.2
```

**Note:** `config.env` must be populated via `bootstrap-config.sh` before this step. Empty values will produce broken substitutions.

### Step 1 — Create `lockdown/` structure

Move entries from `archive/old-config/` to `lockdown/`:

```
lockdown/
├── lib/                   (NEW — abstraction adapters)
│   ├── clipboard-clear.sh
│   ├── discover-session.py
│   └── terminal
├── allowlist/
│   ├── domains/
│   │   ├── infra.txt
│   │   ├── base.txt
│   │   ├── session.txt
│   │   └── deny.txt
│   └── scripts/
│       ├── allowlist.sh
│       ├── seal.py
│       ├── seal_lib.py
│       ├── verify.sh
│       ├── generate-dnsmasq.sh
│       ├── generate-policies.sh
│       ├── generate-nftables.sh
│       ├── enter-internet-netns    (moved from scripts/)
│       ├── sem.py                  (moved from scripts/)
│       ├── unseal.py               (moved from scripts/)
│       └── setup-internet-netns.sh (moved from scripts/)
├── nftables/
│   ├── nftables.conf.base
│   └── nftables.conf.locked
├── polkit/
│   └── 99-internet-lockdown.rules
├── resolv/
│   └── internet-netns.resolv.conf
├── sudoers/
│   └── 99-mike-tools
├── systemd/
│   └── internet-netns.service
├── grub.d/
│   └── 99-nouveau-gsp.cfg
└── sysctl.d/
    └── 99-internet-netns.conf
```

**What stays in `archive/old-config/`** (already handled by Phase 1):

| Category | Entries |
|---|---|
| Dotfiles | `bashrc`, `copyq/`, `foot/`, `fuzzel/`, `fzf/`, `ranger/`, `sway/`, `waybar/`, `obsidian/` |
| Dev | `github.env`, `github.env.template`, `opencode/`, `zed/` |
| Archived | `README.md`, `ruff.toml`, `container/` (empty) |

**`tools/`** (user-facing utilities):

```
tools/
├── bootstrap-config.sh
├── check-firmware.sh    (moved from scripts/)
└── help.sh              (moved from scripts/)
```

`unseal.sh` — deleted entirely (superseded by `unseal.py`).

### Step 2 — Create `lockdown/lib/` abstraction adapters

**`lockdown/lib/clipboard-clear.sh`:**
```bash
#!/bin/bash
# Adapter: clear clipboard — swap copyq/wl-clipboard here, not in seal
for tool in copyq wl-copy; do
    if command -v "$tool" &>/dev/null; then
        case "$tool" in
            copyq)   copyq clear ;;
            wl-copy) wl-copy --clear ;;
        esac
        exit 0
    fi
done
echo "clipboard-clear: no supported clipboard tool found" >&2
exit 1
```

**`lockdown/lib/discover-session.py`:**
```python
#!/usr/bin/env python3
"""Discover active session type. Swap WM detection here, not in seal."""
import subprocess, sys

# Ordered by preference — add new WMs here
sessions = {
    "sway":     "pgrep -x sway",
    "hyprland": "pgrep -x Hyprland",
    "river":    "pgrep -x river",
}

for name, cmd in sessions.items():
    result = subprocess.run(cmd, shell=True, capture_output=True)
    if result.returncode == 0:
        print(name)
        sys.exit(0)

print("unknown")
sys.exit(1)
```

**`lockdown/lib/terminal`:**
```bash
#!/bin/bash
# Adapter: launch terminal — swap terminal here, not in toggle-nmtui.sh etc.
# Reads from config.env if available, falls back to foot
TERMINAL="${TERMINAL:-foot}"
exec "$TERMINAL" "$@"
```

All adapters are installed to `$(LOCKDOWN_LIB_PATH)/` (e.g., `/usr/local/lib/lockdown/`).

### Step 3 — Template hardcoded values

#### Files with `@USERNAME@`:
- `lockdown/sudoers/99-mike-tools` (every line has `mike ALL=...`)
- `lockdown/polkit/99-internet-lockdown.rules` (`subject.user == "mike"`)
- `lockdown/allowlist/scripts/allowlist.sh` (pgrep/sudo/chown -u mike, ~17 locations)
- `lockdown/allowlist/scripts/seal_lib.py` (`pwd.getpwnam("mike")`, `mike:mike`, ~14 locations)
- `lockdown/allowlist/scripts/verify.sh` (`su - mike`)
- `tools/check-firmware.sh` (if applicable)

#### Files with `@USER_UID@`:
- `lockdown/nftables/nftables.conf.locked` (`skuid 1000`)
- `lockdown/allowlist/scripts/verify.sh` (`skuid 1000`)

#### Files with `@OPENCODE_PATH@`:
- `lockdown/allowlist/scripts/enter-internet-netns` (`/home/mike/.opencode/bin/opencode`)
- `tools/help.sh` (`~/linux_setup/keybindings.md`)

#### Files with `@LOCKDOWN_LIB_PATH@`:
- `lockdown/allowlist/scripts/enter-internet-netns`
- `lockdown/systemd/internet-netns.service`
- `lockdown/allowlist/scripts/unseal.py`

#### Files with `@ALLOWLIST_PATH@`:
- `lockdown/allowlist/scripts/allowlist.sh` (~18 occurrences of `/opt/allowlist`)
- `lockdown/allowlist/scripts/seal.py`
- `lockdown/allowlist/scripts/seal_lib.py`
- `lockdown/allowlist/scripts/verify.sh`
- `lockdown/allowlist/scripts/generate-dnsmasq.sh`
- `lockdown/allowlist/scripts/generate-policies.sh`
- `lockdown/allowlist/scripts/generate-nftables.sh`

#### Files with `@NETNS_*@` or `@DNS_*@`:
- `lockdown/nftables/nftables.conf.base` (`10.0.4.0/30`)
- `lockdown/nftables/nftables.conf.locked` (`10.0.4.0/30`)
- `lockdown/resolv/internet-netns.resolv.conf` (`1.1.1.1`)
- `lockdown/allowlist/scripts/enter-internet-netns`
- `lockdown/systemd/internet-netns.service`
- `lockdown/allowlist/scripts/setup-internet-netns.sh` (IP addresses, subnet)

### Step 4 — Update `seal_lib.py` to use adapters + strict env lookup

The most critical change. Three categories of changes:

#### 4a. Replace hardcoded tool calls with adapters:

```python
# BEFORE (hardcoded):
subprocess.run(["copyq", "clear"])
subprocess.run(["wl-copy", "--clear"])

# AFTER (via adapter):
subprocess.run(["clipboard-clear"])
```

```python
# BEFORE (hardcoded):
subprocess.run(["pgrep", "-u", "mike"])

# AFTER (via adapter):
session = subprocess.run(["discover-session"], capture_output=True).stdout.decode().strip()
```

#### 4b. Replace hardcoded identity with strict env lookup:

```python
# BEFORE (hardcoded):
MIKE = pwd.getpwnam("mike")

# AFTER (strict — no fallback, fails if USERNAME not set):
MIKE = pwd.getpwnam(os.environ["USERNAME"])
```

This raises `KeyError: 'USERNAME'` immediately if `config.env` wasn't sourced. This is intentional — forces proper configuration. No silent misconfiguration.

#### 4c. Adapter PATH resolution:

The adapters are installed to `LOCKDOWN_LIB_PATH` which must be in `$PATH` for seal/unseal. If not in PATH, `seal_lib.py` resolves them relative to `LOCKDOWN_LIB_PATH` from `config.env`:

```python
LOCKDOWN_LIB = os.environ.get("LOCKDOWN_LIB_PATH", "/usr/local/lib/lockdown")
os.environ["PATH"] = f"{LOCKDOWN_LIB}:{os.environ['PATH']}"
```

### Step 5 — `Makefile.lockdown` (separate file, requires sudo)

Create `Makefile.lockdown` at repo root:

```makefile
# Makefile.lockdown — requires: sudo make -f Makefile.lockdown lockdown
include config.env

SUBST := sed -i 's|@USERNAME@|$(USERNAME)|g; s|@OPENCODE_PATH@|$(OPENCODE_PATH)|g; s|@OBSIDIAN_VAULT_PATH@|$(OBSIDIAN_VAULT_PATH)|g; s|@ALLOWLIST_PATH@|$(ALLOWLIST_PATH)|g; s|@LOCKDOWN_LIB_PATH@|$(LOCKDOWN_LIB_PATH)|g; s|@LOCKDOWN_BIN_PATH@|$(LOCKDOWN_BIN_PATH)|g; s|@DNS_PRIMARY@|$(DNS_PRIMARY)|g; s|@DNS_SECONDARY@|$(DNS_SECONDARY)|g; s|@NETNS_SUBNET@|$(NETNS_SUBNET)|g; s|@NETNS_HOST@|$(NETNS_HOST)|g; s|@NETNS_CLIENT@|$(NETNS_CLIENT)|g; s|@USER_UID@|$(USER_UID)|g'

.PHONY: lockdown

lockdown:
	@echo "=== Lockdown ==="
	# backup existing system files
	@BACKUP_DIR="/tmp/lockdown-backup-$$(date +%s)"; \
	mkdir -p "$$BACKUP_DIR"; \
	echo "Backing up to $$BACKUP_DIR"; \
	[ -f /etc/nftables.conf ] && cp /etc/nftables.conf "$$BACKUP_DIR/" || true; \
	[ -f /etc/sudoers.d/99-mike-tools ] && cp /etc/sudoers.d/99-mike-tools "$$BACKUP_DIR/" || true; \
	[ -d /opt/allowlist ] && cp -r /opt/allowlist "$$BACKUP_DIR/" || true; \
	echo "Backup complete."
	# adapters
	mkdir -p $(LOCKDOWN_LIB_PATH)
	cp lockdown/lib/* $(LOCKDOWN_LIB_PATH)/
	chmod 755 $(LOCKDOWN_LIB_PATH)/*
	# allowlist
	mkdir -p $(ALLOWLIST_PATH)/domains $(ALLOWLIST_PATH)/scripts
	cp lockdown/allowlist/domains/* $(ALLOWLIST_PATH)/domains/
	cp lockdown/allowlist/scripts/* $(ALLOWLIST_PATH)/scripts/
	# nftables
	cp lockdown/nftables/nftables.conf.base /etc/nftables.conf
	cp lockdown/nftables/nftables.conf.locked $(ALLOWLIST_PATH)/nftables.conf.locked
	# polkit
	mkdir -p /etc/polkit-1/rules.d
	cp lockdown/polkit/*.rules /etc/polkit-1/rules.d/
	# resolv
	mkdir -p /etc/netns/internet-netns
	cp lockdown/resolv/* /etc/netns/internet-netns/
	# systemd
	cp lockdown/systemd/*.service /etc/systemd/system/
	# sudoers
	mkdir -p /etc/sudoers.d
	cp lockdown/sudoers/* /etc/sudoers.d/
	# sysctl
	mkdir -p /etc/sysctl.d
	cp lockdown/sysctl.d/* /etc/sysctl.d/
	# grub
	mkdir -p /etc/default/grub.d/
	cp lockdown/grub.d/* /etc/default/grub.d/
	# scripts (allowlist — bin)
	cp lockdown/allowlist/scripts/enter-internet-netns $(LOCKDOWN_BIN_PATH)/
	cp lockdown/allowlist/scripts/sem.py $(LOCKDOWN_BIN_PATH)/sem
	cp lockdown/allowlist/scripts/unseal.py $(LOCKDOWN_BIN_PATH)/unseal
	cp lockdown/allowlist/scripts/setup-internet-netns.sh /usr/local/lib/setup-internet-netns.sh
	# template substitution
	$(SUBST) $(LOCKDOWN_BIN_PATH)/enter-internet-netns \
	         $(LOCKDOWN_BIN_PATH)/sem \
	         $(LOCKDOWN_BIN_PATH)/unseal \
	         $(ALLOWLIST_PATH)/scripts/*.sh \
	         $(ALLOWLIST_PATH)/scripts/*.py \
	         $(ALLOWLIST_PATH)/nftables.conf.locked \
	         /etc/sudoers.d/* \
	         /etc/polkit-1/rules.d/* \
	         /etc/nftables.conf \
	         /etc/netns/internet-netns/* \
	         /etc/systemd/system/*.service \
	         /etc/sysctl.d/* \
	         /etc/default/grub.d/* \
	         /usr/local/lib/setup-internet-netns.sh
	# validation
	@echo "Validating sudoers..."
	@visudo -c -f /etc/sudoers.d/99-mike-tools || (echo "ERROR: sudoers validation failed" && exit 1)
	@echo "Validating nftables..."
	@sudo nft -c -f /etc/nftables.conf || (echo "ERROR: nftables validation failed" && exit 1)
	# permissions
	chmod 440 /etc/sudoers.d/99-mike-tools
	chown root:root /etc/sudoers.d/99-mike-tools
	chmod 644 /etc/polkit-1/rules.d/*.rules
	chown root:root /etc/polkit-1/rules.d/*.rules
	chmod 644 /etc/nftables.conf
	chown root:root /etc/nftables.conf
	chmod 644 /etc/systemd/system/*.service
	chown root:root /etc/systemd/system/*.service
	chmod 644 /etc/sysctl.d/*
	chown root:root /etc/sysctl.d/*
	chmod 644 /etc/default/grub.d/*
	chown root:root /etc/default/grub.d/*
	chmod 644 /etc/netns/internet-netns/*
	chown root:root /etc/netns/internet-netns/*
	# reload
	systemctl daemon-reload
	@echo "Lockdown deployed."
```

**Key design decisions:**
- Backup existing files to `/tmp/lockdown-backup-$(date +%s)/` before overwriting
- Validate sudoers with `visudo -c -f` before finalizing
- Validate nftables with `sudo nft -c -f` before restarting
- Set correct permissions after copy (sudoers=440, everything else=644)
- Reload systemd daemon after deploying service files

### Step 5b — `Makefile` tools target

Add a `tools` target to the existing `Makefile` (user-level):

```makefile
.PHONY: tools
tools:
	@echo "=== Tools ==="
	install -D -m 755 tools/check-firmware.sh $(LOCKDOWN_BIN_PATH)/check-firmware
	install -D -m 755 tools/help.sh $(LOCKDOWN_BIN_PATH)/help
	$(SUBST) $(LOCKDOWN_BIN_PATH)/check-firmware \
	         $(LOCKDOWN_BIN_PATH)/help
```

Requires a sudoers entry for passwordless deployment:
```
mike ALL=(root) NOPASSWD: /usr/bin/install -D -m 755 /home/mike/linux_setup/tools/* /usr/local/bin/*
```

### Step 6 — Test

1. Run `sudo make -f Makefile.lockdown lockdown` — verify files land in `/opt/`, `/etc/`, `/usr/local/`
2. Verify backup exists in `/tmp/lockdown-backup-*/`
3. Verify `systemctl daemon-reload` picks up the new service file
4. Verify `visudo -c -f /etc/sudoers.d/99-mike-tools` passes
5. Verify `sudo nft -c -f /etc/nftables.conf` passes
6. Verify `clipboard-clear` works, `discover-session` returns `sway`, `terminal` launches foot
7. Run seal/unseal flow and verify adapters are called instead of hardcoded names
8. Verify `USERNAME` is properly resolved in deployed scripts (grep for hardcoded `mike`)

---

## Files changed / created

**Create**
- `Makefile.lockdown`
- `lockdown/lib/clipboard-clear.sh`
- `lockdown/lib/discover-session.py`
- `lockdown/lib/terminal`

**Move from `archive/old-config/`**
- `lockdown/allowlist/` (domains + scripts)
- `lockdown/nftables/`
- `lockdown/polkit/`
- `lockdown/resolv/`
- `lockdown/sudoers/`
- `lockdown/systemd/`
- `lockdown/grub.d/`
- `lockdown/sysctl.d/`
- `tools/check-firmware.sh`
- `tools/help.sh`

**Delete**
- `archive/old-config/scripts/unseal.sh` (superseded by `unseal.py`)

**Rewrite with `@VARIABLE@` templates**
- `lockdown/sudoers/99-mike-tools`
- `lockdown/polkit/99-internet-lockdown.rules`
- `lockdown/allowlist/scripts/allowlist.sh`
- `lockdown/allowlist/scripts/seal_lib.py`
- `lockdown/allowlist/scripts/verify.sh`
- `lockdown/allowlist/scripts/seal.py`
- `lockdown/allowlist/scripts/generate-dnsmasq.sh`
- `lockdown/allowlist/scripts/generate-policies.sh`
- `lockdown/allowlist/scripts/generate-nftables.sh`
- `lockdown/allowlist/scripts/enter-internet-netns`
- `lockdown/allowlist/scripts/unseal.py`
- `lockdown/allowlist/scripts/setup-internet-netns.sh`
- `lockdown/nftables/nftables.conf.base`
- `lockdown/nftables/nftables.conf.locked`
- `lockdown/resolv/internet-netns.resolv.conf`
- `lockdown/systemd/internet-netns.service`
- `tools/check-firmware.sh`
- `tools/help.sh`

**Modify**
- `config.env` (add lockdown vars)
- `Makefile` (add `tools` target)

**NOT modified**
- `install.sh` (broken after Phase 2, deferred to Phase 3)
