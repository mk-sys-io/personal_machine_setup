# Issue Tracker

## [1] GitHub API rate limit blocks localsend/obsidian install

**Status**: Open

**Description**: `install.sh` fetches the latest `.deb` URL from the GitHub API for localsend and obsidian. The unauthenticated GitHub API is limited to 60 requests/hour. By the time install.sh reaches these blocks, earlier curl calls (Brave key, Chrome key, opencode, Zed, tle) often exhaust the budget, causing the API to return 403.

**Impact**:
- Localsend: skipped with `WARNING: Could not determine latest LocalSend URL, skipping`
- Obsidian: skipped with `WARNING: Could not determine latest Obsidian URL, skipping`
- Non-critical — both are optional tools; script continues normally

**Root cause**:
- `set -euo pipefail` + raw GitHub API call without auth token
- 60 req/h unauthenticated rate limit consumed by earlier script steps

**Fix**:
- Add optional `GITHUB_TOKEN` support to authenticate API calls (5000 req/h):
  ```
  GITHUB_AUTH=""
  if [ -n "${GITHUB_TOKEN:-}" ]; then
      GITHUB_AUTH="-H Authorization: token $GITHUB_TOKEN"
  fi
  ```
- The `|| true` guard (already added) prevents `set -e` abort when the call fails

**Workaround**: Set `GITHUB_TOKEN` in environment before running install.sh, or simply re-run install.sh later when the rate limit resets.

---

## [2] CopyQ alternatives discussion

**Status**: Open

**Description**: CopyQ is the current clipboard manager, but it has
drawbacks — Qt dependency, complex UI for a simple clipboard, and
occasional paste delays on Wayland. Evaluate lighter alternatives
(cliphist, wl-clipboard + custom script, etc.) that integrate better
with a minimal Sway environment.

---

## [3] NumLock on by default at boot

**Status**: Closed — implemented.

**Implementation**: Added `xkb_numlock enabled` to the `input *` block in `.config/sway/sway_config` line 155. Sway applies this during startup — no external packages or boot scripts needed.

**Description**: NumLock is off after boot on a fresh Debian + Sway
install. Hardware numlock key press works but is manual every time. No
consistent mechanism across Sway/Wayland to set it on login. Need a
udev rule or early boot script.

---

## [4] Refactor system documentation

**Status**: Open

**Description**: Documentation is scattered across multiple files
(`docs/white_internet_policy.md`, `docs/phase4.md`, `manual_work.md`,
`observations.md`, `issue_tracker.md`) with overlapping content and no
clear separation of audience (user vs developer vs architecture).
Propose consolidating into a standard structure:

- `README.md` — quickstart / what is this
- `docs/decisions.md` — ADR-style architecture decision log
- `docs/` — reference (phase4, allowlist, root ownership inventory, etc.)
- `CHANGELOG.md` — history of changes
- Retire stale files (`observations.md`, `plan.md`)

---

## [5] iwlwifi firmware/kernel version drift monitoring

**Status**: Closed — implemented.

**Implementation**:
- `install.sh` deploys `.config/scripts/check-firmware-drift.sh` to
  `/usr/local/bin/check-firmware` which checks loaded iwlwifi firmware
  version vs package files, and microcode revision. No alias needed —
  `/usr/local/bin/` is in `$PATH`. (See docs/utility-scripts.md.)
- `install.sh` also configures `iwlwifi power_save=0 uapsd_disable=1`
  and `iwlmvm power_scheme=1` via `/etc/modprobe.d/iwlwifi-opt.conf`.

---

## [6] Time-based domain blocking: DNS-level vs browser extension

**Status**: Open

**Description**: Feature request to allow domains in the allowlist to be
blocked/unblocked on a schedule (e.g. block social media during work hours).

**DNS-level approach** (not recommended):
- Annotate `allowlist.txt` entries with time labels, e.g. `*.youtube.com workhours`
- Systemd timer to regenerate dnsmasq config + reload at schedule boundaries
- Pro: system-wide enforcement across all apps/browsers
- Con: significant new code, root-only, limited to whole-domain granularity

**Browser extension approach** (recommended):
- Use Lee​chBlock or similar for per-domain time scheduling
- Pro: minutes to set up, path-level granularity, per-browser profiles, no root
- Con: bypassable via another browser; doesn't affect CLI tools

**Decision**: Browser extensions are sufficient for a single-machine setup.
Filed for reference if system-wide enforcement is ever needed.

---

## [7] Escape hatch for temporary domain additions (v2)

**Status**: Open

**Description**: A multi-layer gated escape hatch. Adding a temporary
domain requires: reason selection → LLM eval → cooldown → time gate →
auto-reset. Maximum friction against impulse, deliberate path for need.

**Flow**:

```
allowlist request <domain>

  1. REASON SELECTION
     [TBD — defined at implementation]
     Categorical, no free-text input.

  2. LLM EVALUATION (cloud, multi-provider fallback)
     Each provider independently allowlisted in infra.txt.
     "Does <domain> reasonably serve <reason>?"
     → Approve → proceed to cooldown
     → Reject → print LLM reasoning, log to seal.log

     No permanent blacklist. All LLM decisions are ephemeral
     and logged for post-hoc review at unseal/recovery.

  3. COOLDOWN (configurable, default 15 min)
     Domain queued, not yet resolving.
     User can cancel via `allowlist cancel <domain>`.
     Prevents impulsive same-session unlocks.

  4. TIME GATE (daylight hours, configurable before seal)
     Default: 09:00–17:00.
     Configured once before final seal; changes require unseal.
     Outside this window → temp domains return NXDOMAIN.
     Night hours are blocked — this is when all impulses trigger.

  5. AUTO-RESET (reboot OR 24h)
     All temp domains cleared. Next session starts clean.
     No persistent state across days.
```

**Design details**:

- **New file**: `/opt/allowlist/allowlist.temp.txt` — managed by request
  flow, cleared on reset. Format: `domain timestamp reason_tag`
- **Time gate**: enforced in `generate-dnsmasq.sh` — skips temp file
  lines when `$(date +%H)` is outside configured window
- **Cooldown**: `at` job fires to move domain from queue → active.
  Cancel before job fires = never activates
- **LLM layer**: multiple cloud providers (Claude, GPT, Gemini) with
  fallback. Each provider domain allowlisted in `infra.txt`. Prompt is
  structured (domain + reason category), no free-text input — kills injection
- **No permanent deny.txt writes**: all rejections logged to `seal.log`
  with full LLM reasoning. User reviews during unseal window
- **Night impulse protection**: if a requested domain's cooldown expires
  after the time gate closes, activation is deferred to the next window
  opening — it doesn't activate at night

**Required changes**:
- `.config/allowlist/scripts/allowlist.sh` — add `request`, `cancel`, `pending` subcommands
- `.config/allowlist/scripts/evaluate-domain.py` — new, calls LLM APIs in fallback
  chain, returns approve/reject
- `.config/allowlist/scripts/generate-dnsmasq.sh` — conditionally include temp.txt
  lines within time gate
- `.config/allowlist/domains/temp.txt` — new file (empty)
- `.config/allowlist/domains/infra.txt` — add LLM provider API endpoints
- `install.sh` — deploy new files
- `manual_work.md` — document `allowlist request`

**Open questions** (answered):
| Question | Decision |
|----------|----------|
| Predefined reasons? | TBD at implementation |
| LLM provider? | Cloud, multi-provider fallback, allowlisted |
| Permanent blacklist? | No — log only, review at unseal |
| Cooldown configurable? | Yes |
| Time gate? | Daylight hours, configurable before seal |

**Status**: Open — not implemented.

---

## [8] Browser-independent web search for locked mode

**Status**: Open

**Description**: When locked, the browser can only reach allowlisted
domains, making web search effectively impossible. The user needs a
terminal-based search tool that works under lockdown without unblocking
general browsing.

**Recommended approach**: Python CLI wrapper around DuckDuckGo Lite
(HTML-only frontend at `lite.duckduckgo.com`). Add a single domain to
`infra.txt` and use a lightweight script.

**UX**:
```
$ search where do ospreys nest
  1. Osprey | Audubon Field Guide
  2. Osprey - Wikipedia
  3. Osprey nesting habits - RSPB

$ search --open 2      → opens Wikipedia in browser (allowlisted or not)
$ search --raw         → prints raw URLs, no indexing
```

**Implementation sketch**:
- New file: `.config/allowlist/scripts/search.py` (~100 lines)
- Uses `html.parser` from stdlib (no pip deps) to parse DuckDuckGo Lite results
- Subcommand `search` in `allowlist.sh` that delegates to `search.py`
- Add `lite.duckduckgo.com` to `infra.txt` (text-only page, low distraction risk)

**Why not ddgr**:
- `ddgr` hits `duckduckgo.com` (general browsing domain, too wide)
- `ddgr` is a third-party tool with its own dependencies and update cycle
- A 100-line Python stdlib script is zero-maintenance once written

**Why not just add DDG to session**:
- Opening the browser is a visual context switch and temptation
- Terminal stays in flow

**Required changes**:
- `.config/allowlist/scripts/search.py` — new file
- `.config/allowlist/scripts/allowlist.sh` — add `search` subcommand wrapper
- `.config/allowlist/domains/infra.txt` — add `lite.duckduckgo.com`
- `install.sh` — deploy search.py to `/opt/allowlist/`
- `manual_work.md` — document `search`

**Status**: Open — not implemented.

---

## [9] AI-assisted domain discovery for broken functionality

**Status**: Open

**Description**: When a page loads but images, embeds, or SSO are
broken under lockdown, identifying the missing domain is manual and
tedious (HAR export, grep through waterfalls, trial-and-error).
Automate this with a `diagnose` command.

**Design**:
- `allowlist diagnose [--for 30] [--llm]` — captures NXDOMAIN queries
  from a timed window, groups unique domains, optionally pipes to an
  LLM for filtering.

**Mechanism**:
1. User triggers `allowlist diagnose --for 45` (45-second window)
2. User reproduces the broken action (visit page, trigger load)
3. Script runs `sudo tcpdump -i lo -nn 'udp port 53'` for 45 seconds
4. Parses output for queries that got SERVFAIL / NXDOMAIN / no response
5. Groups unique domains with frequency count
6. Prints:
   ```
   Diagnosed 12 failed queries in 45s

   Suggested domains to allowlist:
     cdn.socialmediaembeds.com     (8 queries)
     widgets.someapi.com           (3 queries)
     pixel.tracking.com            (1 query)  ← likely tracking

   Run: allowlist open cdn.socialmediaembeds.com
   ```
7. With `--llm` flag: pipes domain list + mini-prompt to `opencode` or `ollama`
   asking "classify each as functional or tracking/ad"

**Privilege**: `tcpdump` needs `sudo` + `CAP_NET_RAW` on loopback.
Add `tcpdump` to `99-mike-tools` sudoers with NOPASSWD.

**Data flow**:
```
tcpdump (loopback, port 53, 45s)
  → parse: extract query names, status bits
  → filter: only domains NOT in any allowlist
  → group + count
  → [optional] LLM classification prompt
  → print results
```

**Required changes**:
- `.config/allowlist/scripts/allowlist.sh` — add `diagnose` function
- `.config/allowlist/scripts/diagnose.py` — new script (tcpdump wrapper + parser)
  Python for output formatting and optional LLM integration
- `.config/sudoers/99-mike-tools` — add `/usr/sbin/tcpdump` NOPASSWD
- `install.sh` — add `tcpdump` to apt packages, deploy diagnose script
- `manual_work.md` — document `diagnose`

**Caveats**:
- Only captures DNS-layer failures. HTTPS failures (TLS, cert) won't show.
- In unrestricted mode (no lockdown), tcpdump will show all queries,
  overwhelming the output. Only useful in locked mode.
- Doesn't capture traffic from containers (they bypass dnsmasq directly to 1.1.1.1)

**Status**: Open — not implemented.

---

## [10] Bash-to-Python migration criteria and audit

**Status**: Open

**Description**: Several scripts have grown past bash's sweet spot.
This issue establishes a general criteria for deciding *when* a bash
script should be converted to Python, then audits all scripts against it.

**Criteria for migration** (any one triggers evaluation):
1. **Construction of structured data formats** (JSON, XML, YAML)
   — bash string interpolation is fragile; missing escape breaks silently
2. **Multi-file logic with >300 lines** — bash has no module system;
   readability degrades sharply past ~200 lines of non-trivial logic
3. **Structured output consumed by other tools** — bash `echo`-based
   output formats are brittle; Python dicts/lists are trivially serializable
4. **Regular expression complexity** — `sed -E`/`grep -P` one-liners
   become unreadable past a certain point; Python re module is more
   readable and testable
5. **Error handling beyond exit codes** — bash has no try/catch;
   cleanup on failure requires trap gymnastics that are easy to get wrong

**Script audit**:

| Script | Lines | Structured data | Complex logic | Migrate? |
|--------|-------|----------------|---------------|----------|
| `allowlist.sh` | 423 | No (flat text) | Yes (10 subcommands, seal flow, state machine) | **Evaluate** — #7, #8, #9 will add more; if CLI keeps growing, migrate |
| `generate-policies.sh` | ~80 | Yes (JSON) | No | **Yes** — JSON construction is fragile, easy win |
| `generate-dnsmasq.sh` | 72 | No | No | **No** — simple, stable, one purpose |
| `generate-nftables.sh` | 33 | No | No | **No** — negligible complexity |
| `verify.sh` | ~80 | No | Yes (10 checks) | **Maybe** — stable, works, low priority |
| `diagnose.py` (new) | — | Yes (structured) | Yes (parsing + optional LLM) | **Write in Python from the start** |
| `search.py` (new) | — | Yes (parsed HTML) | Moderate | **Write in Python from the start** |
| `unseal.sh` | ~30 | No | No | **No** — trivial wrapper |
| `check-firmware-drift.sh` | ~100 | No | Yes (privilege escalation, 3-way log fallback, colored output) | **Yes** — multiple error paths and fallbacks make bash fragile; Python `subprocess.run()` with explicit `check=` is safer |
| `install.sh` | 361 | No | Yes (install orchestration) | **Evaluate** — high line count but linear logic; bash is adequate |

**Recommended order**:
1. Write new search.py and diagnose.py in Python (no migration needed)
2. Migrate `generate-policies.sh` to Python (easy, high safety gain)
3. Re-evaluate `allowlist.sh` after escape hatch is implemented — if
   it crosses 500 lines or adds more structured output, migrate to Python
4. Migrate `check-firmware-drift.sh` to Python (growing complexity — dmesg escalation, dpkg parsing, color output — exceeds bash's safety margin for error handling)
5. Leave everything else in bash

**Non-goals**:
- Not a wholesale rewrite. Keep bash where it works well.
- Not a style debate. Criteria above are pragmatic, not aesthetic.
- The Python requirement is stdlib-only. No pypi dependencies.
  Debian 13 ships with python3 + json/html.parser/argparse in stdlib.

**Prerequisites**: Python3 is already available in Debian 13 and already
used by `verify.sh` for inline DNS tests. `install.sh` would copy `.py`
files to `/opt/allowlist/` with the same `chmod 750` treatment — no
infrastructure changes needed.

**Status**: Open — no urgency, filed for next refactor cycle.

---

## [11] Per-app network access: Opencode/Vane in containers, Obsidian via cgroup + dedicated dnsmasq

**Status**: Open

**Description**: The current allowlist is a single flat list shared by every process under UID 1000. This breaks when different tools need fundamentally different domain sets. Trying to cram OpenCode's search API domains, Obsidian plugin CDNs, Vane's general internet access, and browser browsing domains into one list is futile — it either leaves tools broken or bloats the list until lockdown is meaningless.

**Chosen approach**: Hybrid — two mechanisms, selected by tool type.

---

### CLI tools (Opencode, Vane) → Podman containers

Containers already bypass the DNS lockdown (`/etc/containers/containers.conf` sets `dns_servers = ["1.1.1.1"]`). Running these tools in containers gives them full internet access with zero DNS infrastructure changes.

**Implementation**:

- **Opencode**: Podman alias in `.bashrc`:
  ```bash
  alias opencode='podman run --rm -it \
    --dns 1.1.1.1 \
    -v /home/mike:/home/mike:Z \
    -w /home/mike \
    debian:bookworm-slim \
    /home/mike/.opencode/bin/opencode'
  ```
  Config persists via bind-mount of `$HOME`.

- **Vane**: Same pattern — alias that bind-mounts workspace + config, uses container DNS.

**No new DNS infrastructure needed for CLI tools.**

---

### Obsidian (GUI Electron app) → cgroup + dedicated dnsmasq

Electron in a container is too fragile (Wayland, GPU, D-Bus, seccomp, AppArmor, clipboard, file picker all need passthrough). Obsidian gets its own cgroup and a dedicated dnsmasq instance with its own allowlist.

**Components**:

1. **Cgroup**: `/sys/fs/cgroup/user.slice/user-1000.slice/apps/obsidian/` — created on boot by a root oneshot service, `chown`'d to mike.
2. **Launcher wrapper**: `cage obsidian /usr/bin/obsidian` — writes PID to cgroup `cgroup.procs`, then execs. Desktop file override at `~/.local/share/applications/obsidian.desktop`.
3. **Dedicated dnsmasq**: `dnsmasq-app@obsidian.service` listens on `127.0.0.1:1054`, uses `/etc/dnsmasq.d/app-obsidian.conf` with its own allowlist from `/opt/allowlist/allowlist.obsidian.txt`.
4. **nftables redirect**: When locked, a `nat` rule matches packets from the obsidian cgroup and redirects port 53 → `:1054`:
   ```nft
   socket cgroupv2 "user.slice/user-1000.slice/apps/obsidian" \
       udp dport 53 redirect to :1054
   ```
5. **Obsidian allowlist**: Maintained separately. Only domains needed by Obsidian plugins go here — browser browsing domains stay in the main allowlist.

---

### Required changes

**CLI tool containers**:
- `.bashrc` — add podman aliases for opencode and vane

**Cgroup infrastructure**:
- `.config/allowlist/scripts/setup-app-cgroups.sh` — new, creates cgroup dirs
- `.config/systemd/setup-app-cgroups.service` — new oneshot
- `install.sh` — deploy service and script

**Obsidian dnsmasq**:
- `.config/allowlist/domains/allowlist.obsidian.txt` — new
- `.config/allowlist/scripts/generate-dnsmasq.sh` — accept app name param for per-app configs
- `.config/systemd/dnsmasq-app@.service` — service template
- `.config/nftables/nftables.conf.locked` — add `app-dns` nat table with cgroup redirect

**Obsidian launcher**:
- `/usr/local/bin/cage` — new generic cgroup launcher
- `~/.local/share/applications/obsidian.desktop` — override Exec to use cage

**Integration**:
- `.config/allowlist/scripts/allowlist.sh` — `lock`/`unlock` manages obsidian dnsmasq; `status` shows obsidian allowlist count
- `.config/allowlist/scripts/verify.sh` — add obsidian checks (cgroup, dnsmasq, nftables)
- `manual_work.md` — document container aliases and caged obsidian

**Status**: Open — not implemented.

---

## [12] NVIDIA proprietary driver — attempted, failed (see docs/nvidia-attempt.md)

**Status**: Closed — failed. System rendered unusable, restored via Timeshift.
All changes documented in `docs/nvidia-attempt.md`.

---

## [13] Unified system control panel — CLI entry point for keybindings, aliases, diagnostics, unseal

**Status**: Open

**Description**: The `.config/scripts/` directory is the staging ground for a unified CLI tool that aggregates all user-facing system commands into a single discoverable interface. Currently these are scattered across separate scripts and mental shortcuts.

**Scope**:
- **Keybindings reference** — `panel keys` renders `keybindings.md` via glow
- **Alias listing** — `panel aliases` prints custom bash aliases
- **Hardware health** — `panel health` runs firmware drift check, GPU status, battery
- **Unseal** — `panel unseal` delegates to the unseal wrapper
- **System info** — `panel info` prints kernel, GPU driver, uptime, disk

**Design constraints**:
- Written in bash initially, Python if complexity grows
- Each subcommand is a standalone script in `.config/scripts/` so they work individually too
- The `panel` wrapper is just a dispatcher: `panel <subcommand>` → runs matching script

**Required changes** (future):
- `.config/scripts/panel.sh` — dispatcher script
- `.config/scripts/panel-keys.sh`, `panel-health.sh`, etc. — subcommands
- `~/.bashrc` — add `panel` alias

**Status**: Open — not implemented.

---

## [14] Visual layout enhancement: wallpaper, system health, data consumption, update panel

**Status**: Open

**Description**: Aesthetic and operational improvements to the desktop
environment — wallpaper, live system health indicators, network usage
monitoring, and a manual update management subcommand.

### 1. Wallpaper/background
- Add `swaybg` to Sway autostart to display an image wallpaper
- Image path and asset determined during implementation
- Catppuccin-compatible palette preferred

### 2. Waybar temperature module
- Show CPU temperature in the waybar right section
- Live reading from `/sys/class/thermal/thermal_zone*/temp`
- Colored indicator: normal (default), warm (yellow ≥ 75°C), hot (red ≥ 85°C)

### 3. Data consumption counter
- `vnstat` for persistent per-interface traffic logging (survives reboot)
- Waybar module showing daily bandwidth (rx/tx for current day)
- Network interface determined during implementation

### 4. Update panel subcommand
- `panel updates` — checks `apt list --upgradable`, shows package count
  and names, prompts to upgrade
- No automation (no unattended-upgrades); manual invocation only

### Open questions
- Notification daemon (swaync vs mako vs none) — still an idea, decision deferred

### Required changes
- `.config/sway/config` — add `exec swaybg` for wallpaper
- `.config/waybar/config.json` — add `temperature` and `vnstat` custom modules
- `.config/waybar/style.css` — add temperature color classes
- `.config/scripts/panel-updates.sh` — new, update checker subcommand
- `install.sh` — add `swaybg`, `vnstat` to apt packages; deploy panel-updates.sh

**Status**: Open — not implemented.
