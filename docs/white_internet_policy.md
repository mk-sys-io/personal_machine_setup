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

This document covers **Phase 1** only.

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
