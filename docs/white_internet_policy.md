# White Internet Policy — Architecture & Design

## Overview

The **White Internet Policy** is the browser and DNS lockdown layer
of `internet.yaml`. The overall project locks down a Debian 13 /
Sway desktop to a whitelist-only internet model across browser,
DNS, and kernel layers, while keeping a usable development
environment for regular work.

Four phases:

| Phase | What |
|---|---|
| **1** Browser Enterprise Lockdown | Brave + Firefox policy enforcement |
| **2** Kernel Firewall & PolicyKit | nftables + pkexec lockout |
| **3** Automated Verification | Assert network isolation |
| **4** Manual User Execution | Sudo removal, password rotation |

This document covers **Phase 1** and **Phase 2**.

---

## Phase 1: Browser Enterprise Lockdown

### Goal

Force Brave (primary browser) and Firefox (fallback) to:

- Route all DNS through NextDNS via DoH in **secure** mode
- Strip bloat, telemetry, and developer tools
- Enable system dark mode by default
- Optionally restrict browsing to a curated domain whitelist,
  switchable at runtime without editing config files

The default state after `install.sh` is **unrestricted** — debloat,
DoH, and dark mode are active, but all sites are reachable. The user
runs `allowlist lock` to activate URL filtering.

### Architecture

```
~/.config/allowlist.txt ──┐
~/.config/env ────────────┤
                          ▼
         .config/scripts/generate-policies.sh
                          │
              ┌───────────┼───────────┐
              ▼           ▼           ▼
    /etc/brave/policies/managed/policy.json
    /etc/chromium/policies/managed/policy.json  (copy)
    /etc/opt/chrome/policies/managed/policy.json (copy)
    /etc/firefox/policies/policies.json
              │
              ▼
         allowlist.sh ──► sudo generate-policies.sh --mode locked|unrestricted
```

### Files

| File | Purpose |
|---|---|
| `.config/allowlist.txt` | Single-source domain list (one per line) |
| `.config/env` | Contains `NEXTDNS_CONFIG_ID` — sourced at policy generation |
| `.config/env.template` | Template for `.config/env` |
| `.config/brave/policy.json.template` | Brave/Chromium policy with `{{NEXTDNS_ID}}`, `{{BLOCKLIST}}`, `{{ALLOWLIST}}` placeholders |
| `.config/firefox/policies.json.template` | Firefox policy with same placeholders, uses WebsiteFilter Block/Allow |
| `.config/scripts/generate-policies.sh` | Reads templates + allowlist + env, generates real policies, deploys via sudo, cleans up stale `kiosk_policy.json` |
| `.config/scripts/allowlist.sh` | CLI control interface — user-facing commands |
| `.config/bashrc` | Defines `alias allowlist=~/.config/waybar/scripts/allowlist.sh` |
| `docs/allowlist.md` | Command reference and first-run flow |

### What the Brave template enforces

- **DnsOverHttpsMode**: `"secure"` — no plaintext DNS fallback
- **DnsOverHttpsTemplates**: NextDNS endpoint — template placeholder
  `{{NEXTDNS_ID}}` replaced with real config ID at generation time
- **URLBlocklist** / **URLAllowlist**: Switched based on mode
- **DarkModeAvailability**: `1` (enabled)
- **ExtensionInstallForcelist**: uBlock Origin + Bitwarden
- **DeveloperToolsDisabled**: true
- **PasswordManagerEnabled**: false
- **MetricsReportingEnabled**: false
- **BackgroundModeEnabled**: false
- Plus other debloat settings

### What the Firefox template enforces

- **DnsOverHttps**: `{"Locked": true, "Enabled": true}` with NextDNS
  template URL
- **WebsiteFilter.Block** / **WebsiteFilter.Allow**: Switched based
  on mode
- **DisableDeveloperTools**: true
- **DisableFirefoxStudies**: true
- **DisableTelemetry**: true
- **ExtensionSettings**: empty (no force-install — Firefox addon
  domains not in allowlist)
- Plus other debloat settings

### Design Decisions

**Single-source allowlist drives both browsers.**
`allowlist.txt` contains only the bare domain (e.g. `github.com`).
`generate-policies.sh` produces the right format for each browser:
`"github.com"` in Chromium arrays, `"*://github.com/*"` in Firefox
WebsiteFilter entries. One file to edit, both browsers in sync.

**Two modes: locked and unrestricted.**
After `install.sh`, mode is `unrestricted` — empty `URLBlocklist`
and `URLAllowlist`, no URL filtering. The user opts into lockdown
with `allowlist lock`. This prevents `install.sh` from breaking
internet access before the user has configured everything.

**Templates with `{{PLACEHOLDERS}}` instead of static files.**
The original `internet.yaml` wrote a hardcoded `policy.json` with
8 domains and a placeholder NextDNS ID. Using sed-based template
substitution means the allowlist, NextDNS config ID, and mode are
injected at generation time — no manual editing of JSON.

**Stale `kiosk_policy.json` cleanup.**
Chromium merges arrays from *all* managed policy files, not just
one. An old `kiosk_policy.json` from a previous version would
combine with the new `policy.json`, duplicating or conflicting with
settings. The generator removes `kiosk_policy.json` before deploying.

**Firefox as fallback only, no extension force-install.**
uBlock Origin and Bitwarden are force-installed for Brave via
`ExtensionInstallForcelist`. Firefox was excluded because Mozilla's
addon domains (`addons.mozilla.org`, `services.addons.mozilla.org`)
would need to be in the allowlist, weakening the walled garden.
Firefox must be pre-configured manually if needed.

**Default state from `install.sh` is unrestricted.**
`install.sh` ends with `allowlist unlock`, which deploys debloat +
DoH policies with empty block/allow arrays. This means:
- DNS is always forced through NextDNS DoH (secure mode)
- Bloat/telemetry are always stripped
- The user's internet is never accidentally broken
- Lockdown is a conscious choice (`allowlist lock`)

**Real home resolution for sudo context.**
`generate-policies.sh` runs under `sudo`. It resolves the original
user's home directory via `SUDO_USER` + `getent` so templates,
allowlist, and env file are found correctly.

**Temp files under `$HOME/.cache/generate-policies/`.**
Early versions wrote to `/tmp`, which caused Permission denied on
some systems due to AppArmor or restrictive tmpfs mounts. Files are
now created via `mktemp` under the user's home directory and cleaned
up after deploy.

**NextDNS CLI installed from GitHub API, not interactive curl pipe.**
The original `internet.yaml` used `curl -sL https://nextdns.io/install | sh`,
which is interactive. Replaced with direct `.deb` download from
`api.github.com/repos/nextdns/nextdns/releases/latest` so the install
is fully non-interactive. Also added `command -v nextdns` guardrail
to skip if already installed.

**GPG `--batch --yes` to suppress interactive prompts.**
The Brave repository key import would prompt to overwrite if the
keyring already existed. `--batch --yes` makes it silent.

### Usage

For all commands, first-run flow, and verify steps, see
**[`docs/allowlist.md`](allowlist.md)**.

Quick reference:

| Command | What it does |
|---|---|
| `allowlist lock` | Enable URL whitelist |
| `allowlist unlock` | Disable URL whitelist |
| `allowlist toggle` | Switch between locked and unrestricted |
| `allowlist status` | Show current mode and domain count |
| `allowlist add <dom>` | Add domain to allowlist |
| `allowlist remove <dom>` | Remove domain from allowlist |
| `allowlist search <pat>` | Find domains matching pattern |
| `allowlist list` | List all allowed domains |

### Verification

| Check | How |
|---|---|
| Brave policies | `chrome://policy` |
| Firefox policies | `about:policies` |
| NextDNS status | `nextdns status` |
| DNS resolution | `ping -c 2 github.com` |
| Current mode | `allowlist status` |

---

## Phase 2: Kernel Firewall & PolicyKit Lockdown

### Goal

- Prevent DNS leaks from user `mike` by blocking DNS traffic to
  non-NextDNS resolvers at the kernel level (nftables)
- Block `pkexec` escalation for user `mike` via PolicyKit
- No changes to the user interface — `allowlist lock`/`unlock`
  toggles nftables alongside browser policies

### Design Decisions

**DNS-leak prevention only, not full HTTP/HTTPS blocking.**
The original `internet.yaml` specified dropping ALL ports 80/443
traffic from UID 1000 unless destined to NextDNS IPs. This would
block web browsing entirely (websites aren't on NextDNS IPs) and
make the system unusable. Instead, Phase 2 only blocks DNS ports
(53, 853) — the browser's enterprise policies (Phase 1) handle
URL whitelist enforcement at the application layer, while nftables
prevents non-browser processes from bypassing NextDNS for
resolution.

**PolicyKit uses modern `.rules` format, not legacy `.pkla`.**
Debian 13 uses `polkitd` ≥0.106, which reads JavaScript rules
from `/etc/polkit-1/rules.d/`. The old `.pkla` format path
(`/etc/polkit-1/localauthority/`) does not exist on this system.

**PolicyKit is permanent, not toggled.**
Unlike nftables (which switches with `allowlist lock`/`unlock`),
the PolicyKit lockdown is a one-way door — once deployed by
`install.sh`, `pkexec` is permanently blocked for user `mike`.
This removes an escalation path ahead of Phase 4 (sudo removal).

**NextDNS anycast IPs are hardcoded.**
NextDNS uses fixed anycast addresses (`45.90.28.0`, `45.90.30.0`
for IPv4; `2a10:50c0::ad1:ff`, `2a10:50c0::ad2:ff` for IPv6).
These are not per-user and do not change, so they are hardcoded
in the nftables locked template rather than sourced from env.

**nftables uses the existing `/etc/nftables.conf` — not a separate
include.**
The base skeleton already existed at `/etc/nftables.conf` from the
`nftables` package install. The `generate-nftables.sh` script
replaces the entire file with either the base skeleton (unrestricted)
or the locked ruleset, then restarts the service.

**CLI DNS tools break when locked (intentional).**
When locked, `dig`, `nslookup`, `curl`, `ping`, and any other CLI
tool run as user `mike` cannot resolve DNS because their queries
to the router (or any non-NextDNS resolver) are dropped at the
kernel level. Use `sudo` for CLI tools that need DNS, or configure
the system resolver to `127.0.0.1` (the NextDNS local proxy on
port 53). Browser DoH is unaffected.

### Files

| File | Purpose |
|---|---|
| `.config/nftables/nftables.conf.base` | Base skeleton with empty accept-all chains |
| `.config/nftables/nftables.conf.locked` | Same skeleton + DNS-leak prevention rules for UID 1000 |
| `.config/scripts/generate-nftables.sh` | Copies the right template to `/etc/nftables.conf`, restarts service |
| `.config/polkit/99-internet-lockdown.rules` | JavaScript PolicyKit rule blocking pkexec for user mike |

### nftables locked ruleset

```nft
table inet filter {
        chain output {
                type filter hook output priority filter; policy accept;

                oifname "lo" accept                       # localhost
                oifname "cni+" accept                     # container interfaces
                oifname "docker+" accept

                # Block DNS leaks from user mike (UID 1000)
                skuid 1000 udp dport { 53, 853 } ip daddr != { 45.90.28.0, 45.90.30.0 } drop
                skuid 1000 tcp dport { 53, 853 } ip daddr != { 45.90.28.0, 45.90.30.0 } drop
                skuid 1000 udp dport { 53, 853 } ip6 daddr != { 2a10:50c0::ad1:ff, 2a10:50c0::ad2:ff } drop
                skuid 1000 tcp dport { 53, 853 } ip6 daddr != { 2a10:50c0::ad1:ff, 2a10:50c0::ad2:ff } drop
        }
}
```

### PolicyKit rule

```javascript
polkit.addRule(function(action, subject) {
    if (subject.user == "mike") {
        return polkit.Result.NO;
    }
});
```

### Verification

| Check | How |
|---|---|
| nftables ruleset | `sudo nft list ruleset` (should show DNS drop rules when locked) |
| nftables mode | `sudo systemctl status nftables` |
| PolicyKit active | `pkexec id` as user `mike` (should fail with "not authorized") |
| DNS leak (locked) | `dig @192.168.1.1 github.com` as user `mike` (should timeout) |
| DNS leak (unlocked) | Same command should succeed |
