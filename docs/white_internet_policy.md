# White Internet Policy — Architecture & Design

## Overview

The **White Internet Policy** is the DNS-level lockdown layer
of `internet.yaml`. The overall project locks down a Debian 13 /
Sway desktop to a whitelist-only internet model across DNS,
kernel, and browser layers, while keeping a usable development
environment for regular work.

Four phases:

| Phase | What |
|---|---|
| **1** Browser Debloat + DNS Architecture | Debloated Brave/Chrome/Firefox policies, dnsmasq DNS proxy |
| **2** Kernel Firewall & PolicyKit | nftables + pkexec lockout |
| **3** Automated Verification | Assert network isolation |
| **4** Seal & Sudo Removal | Recovery credentials, podman + tle recovery |

This document covers **Phases 1–4**.

---

## Phase 1: Browser Debloat + DNS Architecture

### Goal

- Strip bloat, telemetry, and developer tools from Brave and Firefox
- Route all DNS through a local dnsmasq proxy that enforces a
  domain whitelist when locked
- Prevent browser DoH bypasses by disabling the feature at the
  policy level
- No NextDNS dependency — dnsmasq forwards to Cloudflare `1.1.1.1`

The default state after `install.sh` is **unrestricted** — all
domains resolve via dnsmasq → `1.1.1.1`. The user runs
`allowlist lock` to activate DNS whitelisting. `allowlist verify`
provides comprehensive system checks.

### Architecture

```
                        ┌───────────────────┐
                        │  /etc/resolv.conf  │
                        │  127.0.0.1         │ (immutable, chattr +i)
                        └────────┬──────────┘
                                 │
                                 ▼
                        ┌───────────────────┐
                        │  dnsmasq:53        │  UID 0
                        │  /etc/dnsmasq.d/   │
                        │  99-allowlist.conf │
                        └────────┬──────────┘
                    ┌────────────┼────────────┐
                    ▼            ▼            ▼
            locked mode    unlocked mode   upstream
            ┌─────────────────────┐        1.1.1.1
            │ per-domain forward  │    ┌──────────┐
            │ server=/dom/1.1.1.1 │    │ 1.1.1.1  │
            │ no default = NXDOMAIN│    └──────────┘
            └─────────────────────┘

/opt/allowlist/allowlist.txt ──► generate-dnsmasq.sh
/opt/allowlist/brave-policy.json.template ──► generate-policies.sh
/opt/allowlist/firefox-policies.json.template ──► generate-policies.sh

nftables (locked): skuid 1000 → port 53/853 → DROP (except loopback)
```

### Files

| File | Purpose |
|---|---|
| `/opt/allowlist/allowlist.txt` | Single-source domain list (one per line, `*.` prefix supported) |
| `/opt/allowlist/generate-dnsmasq.sh` | Reads allowlist.txt + mode, writes `/etc/dnsmasq.d/99-allowlist.conf`, restarts dnsmasq |
| `/opt/allowlist/brave-policy.json.template` | Static debloat-only Brave policy (no URL filtering, no DoH) |
| `/opt/allowlist/firefox-policies.json.template` | Static debloat-only Firefox policy (no WebsiteFilter, no DoH) |
| `/opt/allowlist/generate-policies.sh` | Copies static templates to browser policy directories |
| `/opt/allowlist/allowlist.sh` | CLI control interface |
| `/opt/allowlist/verify.sh` | System verification script (9 checks) |
| `/opt/allowlist/nftables.conf.base` | Base nftables skeleton for unrestricted mode |
| `/opt/allowlist/nftables.conf.locked` | nftables DNS-leak prevention ruleset for locked mode |
| `/opt/allowlist/generate-nftables.sh` | Copies the right nftables template to `/etc/nftables.conf` |
| `docs/allowlist.md` | Command reference and first-run flow |

### What the Brave template enforces (also deployed to Chrome)

The same `policy.json.template` is deployed to both Brave
(`/etc/brave/policies/managed/`) and Chrome
(`/etc/opt/chrome/policies/managed/`). Chrome silently ignores
Brave-specific keys — no separate template needed.

- **DnsOverHttpsMode**: `"off"` — explicit DoH disable
- **BuiltInDnsClientEnabled**: `false` — forces OS resolver
- **IncognitoModeAvailability**: `0` (enabled)
- **ExtensionInstallForcelist**: uBlock Origin + Bitwarden
- **PasswordManagerEnabled**: false
- **DefaultGeolocationSetting**: `2` (block)
- **MetricsReportingEnabled**: `false`
- **BackgroundModeEnabled**: `false`
- **SafeBrowsingProtectionLevel**: `0` (disabled — dnsmasq is the
  filter layer, not Google Safe Browsing)
- **HideFirstRunExperience**: `true`
- All Brave bloat disabled (Rewards, Wallet, VPN, Leo AI, Tor,
  News, Talk, Speedreader, Wayback Machine, P3A, Stats, Discovery,
  Playlist)

### What the Firefox template enforces

- **Preferences**: `network.trr.mode` = 5 (TRR off, system DNS only)
- **DisableTelemetry**: true
- **DisableFirefoxStudies**: true
- **DisablePocket**: true
- **DisableAccounts**: true
- **NoDefaultBookmarks**: true

### Design Decisions

**Domain whitelisting moved from browser URL policies to dnsmasq.**
Chrome's `URLBlocklist`/`URLAllowlist` silently drops all wildcard
patterns (`*.domain` and `[*.]domain`). dnsmasq's `server=/domain/upstream`
matches the domain and all subdomains natively — no wildcard syntax
needed. This provides true DNS-level whitelisting that affects all
applications, not just browsers.

**NextDNS dropped, upstream is Cloudflare 1.1.1.1.**
NextDNS added complexity (daemon install, config ID env file, port
juggling between 53 and 5353, profile setup). dnsmasq handles the
whitelisting directly. In locked mode, non-allowlisted domains get
NXDOMAIN regardless of upstream — so NextDNS blocklists added no
value. In unlocked mode, 1.1.1.1 is fast and no-account-needed.

**resolv.conf is immutable.**
`chattr +i /etc/resolv.conf` prevents NetworkManager from overwriting
it. NetworkManager is also configured with `dns=none` to avoid
conflicts.

**Browser DoH explicitly disabled.**
Without this, browsers could bypass dnsmasq entirely by using DoH
on port 443 (not blocked by nftables). `DnsOverHttpsMode: "off"`
in Brave and `network.trr.mode: 5` in Firefox ensure system DNS is
always used.

**dnsmasq config is mode-generated.**
`generate-dnsmasq.sh` writes per-domain `server=/domain` entries
when locked (no default server → NXDOMAIN for non-allowlisted), and
a wildcard `server=1.1.1.1` when unlocked. `no-resolv` prevents
dnsmasq from falling back to system resolvers.

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

**Chrome as secondary browser for educational compatibility.**
Brave is the primary browser, but the University of the People
(UoPeople) Moodle-based LMS has layout and functionality issues in
Brave that do not occur in Chrome. Chrome is installed from the
official Google apt repo alongside Brave as a targeted fallback for
coursework. The same policy template is deployed to both — Chrome
ignores Brave-specific keys silently, while both enforce DoH
disable, built-in DNS client off, geolocation blocked, no
telemetry, and no background mode. Extension force-install (uBlock
Origin + Bitwarden) applies to both.

**Default state from `install.sh` is unrestricted.**
`install.sh` installs all packages and deploys the allowlist
utility, but does **not** generate browser policies or lock the
system. The user configures domains first, then explicitly locks.
This prevents `install.sh` from breaking internet access before
setup is complete.

**All scripts are root-owned under `/opt/allowlist/`.**
From day one, the allowlist utility lives at `/opt/allowlist/` owned
by `root:root`. There is no sudoers rule granting special access —
while mike has sudo, he invokes it via `sudo /opt/allowlist/allowlist.sh`.
After sudo removal, recovery is exclusively via podman + tle.

**Temp files under `/tmp/generate-policies/`.**
Since the generate script now runs as root (scripts are root-owned),
`/tmp/` is always writable. No Permission denied issues.

**dnsmasq replaces NextDNS CLI entirely.**
The original design used NextDNS as a local DoH-to-DNS proxy on
port 53, with the browser's enterprise policies enforcing URL
whitelists and DoH. This didn't work for three reasons:
1. Chrome silently drops wildcard entries in `URLAllowlist`
2. Browser DoH bypasses the local proxy (runs on port 443)
3. NextDNS required an additional daemon, config ID, and account
   setup

The new design uses dnsmasq on port 53 with mode-generated config.
It either forwards only allowlisted domains (locked) or everything
(unrestricted). No accounts, no extra daemon, no browser bypass
vector.

**GPG `--batch --yes` to suppress interactive prompts.**
The Brave repository key import would prompt to overwrite if the
keyring already existed. `--batch --yes` makes it silent.

### Usage

For all commands, first-run flow, and verify steps, see
**[`docs/allowlist.md`](allowlist.md)**.

All commands run from a root shell (`su -`). Before sudo removal, prefix with `sudo`.

Quick reference:

| Command | What it does |
|---|---|---|
| `/opt/allowlist/allowlist.sh lock` | Enable DNS whitelist (dnsmasq + nftables) |
| `/opt/allowlist/allowlist.sh unlock` | Disable DNS whitelist |
| `/opt/allowlist/allowlist.sh toggle` | Switch between locked and unrestricted |
| `/opt/allowlist/allowlist.sh status` | Show current mode and domain count |
| `/opt/allowlist/allowlist.sh add <dom>` | Add domain to allowlist |
| `/opt/allowlist/allowlist.sh remove <dom>` | Remove domain from allowlist |
| `/opt/allowlist/allowlist.sh search <pat>` | Find domains matching pattern |
| `/opt/allowlist/allowlist.sh list` | List all allowed domains |
| `/opt/allowlist/allowlist.sh verify` | Run full system verification |
| `/opt/allowlist/allowlist.sh seal` | Seal recovery credentials with timelock encryption |

### Verification

| Check | How |
|---|---|
| Brave policies | `chrome://policy` |
| Firefox policies | `about:policies` |
| dnsmasq status | `systemctl status dnsmasq` |
| DNS resolution | `ping -c 2 github.com` or `sudo /opt/allowlist/allowlist.sh verify` |
| Current mode | `sudo /opt/allowlist/allowlist.sh status` |

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

**nftables blocks ALL external DNS from UID 1000, no IP exceptions.**
Previously, the locked ruleset allowed user `mike` to reach NextDNS
anycast IPs directly (`45.90.28.0`, `45.90.30.0`). Now that dnsmasq
handles all DNS forwarding, there is no reason for user-level
processes to reach any external DNS server. All DNS must go through
`127.0.0.1:53` (dnsmasq). The rules are simpler and more restrictive:
just drop everything to ports 53/853 from UID 1000, with a loopback
exception.

**nftables uses the existing `/etc/nftables.conf` — not a separate
include.**
The base skeleton already existed at `/etc/nftables.conf` from the
`nftables` package install. The `generate-nftables.sh` script
replaces the entire file with either the base skeleton (unrestricted)
or the locked ruleset, then restarts the service.

**CLI DNS tools break when locked (intentional).**
When locked, `dig`, `nslookup`, `curl`, `ping`, and any other CLI
tool run as user `mike` cannot resolve DNS because their queries
to any external resolver are dropped at the kernel level. nftables
`drop` with `skuid` matching can return **EPERM** (`Operation not
permitted`) to the sending socket rather than a silent timeout —
both behaviors confirm the packet was blocked. Root (via `su -`)
is unaffected because nftables only targets UID 1000.

### Files

| File | Purpose |
|---|---|
| `/opt/allowlist/nftables.conf.base` | Base skeleton with empty accept-all chains |
| `/opt/allowlist/nftables.conf.locked` | Same skeleton + DNS-leak prevention rules for UID 1000 |
| `/opt/allowlist/generate-nftables.sh` | Copies the right template to `/etc/nftables.conf`, restarts service |
| `/etc/polkit-1/rules.d/99-internet-lockdown.rules` | JavaScript PolicyKit rule blocking pkexec for user mike |

### nftables locked ruleset

```nft
table inet filter {
        chain output {
                type filter hook output priority filter; policy accept;

                oifname "lo" accept                       # localhost (dnsmasq)

                # Block ALL external DNS from user mike (UID 1000)
                skuid 1000 udp dport { 53, 853 } drop
                skuid 1000 tcp dport { 53, 853 } drop
        }
}
```

The `inet` table matches both IPv4 and IPv6. No IP exception list
is needed — ALL external DNS from UID 1000 is blocked. dnsmasq
(UID 0) bypasses these rules and reaches `1.1.1.1` upstream.
Loopback traffic (127.0.0.1:53) to dnsmasq is allowed via
`oifname "lo" accept`.

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
| nftables ruleset | `sudo nft list ruleset` (should show 2 drop rules when locked) |
| nftables mode | `sudo systemctl status nftables` |
| dnsmasq status | `systemctl status dnsmasq` (should show active) |
| PolicyKit active | `pkexec id` as user `mike` (should fail with "not authorized") |
| DNS leak (locked) | `dig @1.1.1.1 github.com` as user `mike` (should timeout/EPERM) |
| DNS leak (unlocked) | Same command should succeed |
| Full verification | `sudo /opt/allowlist/allowlist.sh verify` |

---

## Phase 3: Automated Environment Verification

### Goal

Confirm all layers are working correctly for the current mode before
proceeding to Phase 4 (irreversible lockdown). Provides a single
command — `verify.sh` — that reports PASS/FAIL per check.

### Rationale: Podman over Docker

Docker rootless depends on `polkit` for its systemd user service.
Phase 2 permanently blocks all PolicyKit actions for user `mike`
(`ResultAny=no`), which would break rootless Docker.

Podman is daemonless and does not require polkit, a root daemon, or
a special group. It uses `/etc/subuid`/`/etc/subgid` for UID
mapping instead. No conflict with Phase 2 or Phase 4 (zero-sudo).

An `alias docker="podman"` is added to `.bashrc` so standard
`docker` commands work without modification.

### Files

| File | Purpose |
|---|---|
| `/opt/allowlist/verify.sh` | Standalone verification script |
| `~/go/bin/tle` or `/usr/local/bin/tle` | Time-locked encryption binary (installed by `install.sh`) |

### Verification checks (`verify.sh`)

| # | Check | How | Expectation |
|---|---|---|---|---|---|
| 1 | Mode file | `/opt/allowlist/mode` | Exists and readable |
| 2 | nftables rules | `sudo nft list ruleset` | Drop rules present when locked, absent when unrestricted |
| 3 | dnsmasq daemon | `systemctl is-active dnsmasq` | "active" |
| 4 | System DNS | `cat /etc/resolv.conf` + `lsattr` | Points to 127.0.0.1, immutable |
| 5 | DNS leak (user) | `socket.getaddrinfo('github.com', 80)` via python | Blocked when locked, reachable when unrestricted |
| 6 | DNS via root | `getent hosts github.com` via root/sudo | Always reachable (bypasses nftables); skips if no cached sudo |
| 7 | Container DNS | `podman run alpine ping 1.1.1.1` | Always works (podman uses its own netns — not isolated by host nftables) |
| 8 | `tle` binary | `~/go/bin/tle` or `/usr/local/bin/tle` | Must exist (Phase 4 prerequisite) |
| 9 | PolicyKit + dnsmasq | Rule file existence + `systemctl is-enabled dnsmasq` | Both must pass |

### What Phase 3 does NOT cover

- Browser policy pages (`chrome://policy`, `about:policies`) —
  requires human inspection
- NextDNS dashboard query logs (my.nextdns.io) — requires
  browser login
- These are documented in `manual_work.md` and
  `docs/allowlist.md`

### Changes to `install.sh` for Phase 3

| Change | Reason |
|---|---|
| `apt install -y podman` | Container runtime, no polkit conflict |
| `apt install -y dnsmasq` | Local DNS proxy for whitelisting |
| `go install .../tle@latest` | Time-locked encryption for Phase 4 root password |
| `cp .config/scripts/generate-dnsmasq.sh` | New script for dnsmasq config generation |
| NM `dns=none` + `chattr +i /etc/resolv.conf` | Prevent resolv.conf overwrite |

### Design Decisions

**Standalone script, also available via `allowlist verify`.**
`verify` is a separate concern from mode toggling. Running it as
`/opt/allowlist/verify.sh` or via `sudo /opt/allowlist/allowlist.sh verify`
provides the same check.

**DNS leak test uses `socket.getaddrinfo()`, not a raw UDP packet.**
The original test constructed a raw DNS query over UDP to the
router's IP, but some routers ignore manually-crafted DNS packets.
`getaddrinfo()` uses the system resolver, is always available
(stdlib, no packages), and tests whether *any* DNS resolution
works — which is what matters for the walled-garden model.

**Container DNS is not isolated by host nftables (expected).**
Rootless podman with the `pasta` network backend uses its own
network namespace that bypasses the host's nftables rules. Host
`skuid`-based iptables/nftables rules cannot match packets inside
a separate network namespace. This is an acknowledged caveat:
containers always reach DNS regardless of `allowlist lock` state.
The verify script skips this check with an explanatory message
rather than reporting a false failure.

---

## Phase 4: Seal & Sudo Removal

### Goal

Make the allowlist lockdown irreversible from user `mike`'s
perspective by:

1. Removing mike from the `sudo` group (manual step)
2. Encrypting recovery credentials (mike's login password + root
   password) with a timelock via `tle`
3. Providing a recovery path via podman container that bypasses
   the host nftables to reach the drand network

### Architecture

```
/opt/allowlist/   (root:root, all files)
├── allowlist.sh              CLI — invoked via sudo (or su - after removal)
├── generate-dnsmasq.sh       dnsmasq config generator (reads allowlist.txt + mode)
├── generate-policies.sh      Browser policy deployer (copies static templates)
├── generate-nftables.sh      nftables ruleset deployer
├── verify.sh                 9-check verification
├── allowlist.txt             Domain whitelist (one per line, supports `*.` prefix)
├── nftables.conf.base        Unrestricted nftables skeleton
├── nftables.conf.locked      Locked nftables DNS-leak rules
├── brave-policy.json.template   Brave/Chromium debloat-only policy template
├── firefox-policies.json.template  Firefox debloat-only policy template
└── mode                      Current mode state (locked/unrestricted)

~mike/.config/sealed-credentials   (mike:mike, 644 — created by `seal`)
```

The allowlist utility belongs to root from day one. While mike has
sudo, all commands use `sudo /opt/allowlist/allowlist.sh`. There is
**no sudoers rule** granting NOPASSWD access, and **no bash alias**.

### Design Decisions

**No sudoers rule, no alias.**
Unlike earlier designs that contemplated a `NOPASSWD` sudoers rule
for allowlist.sh, the final approach is simpler: while mike has
sudo, he types the full path with `sudo`. After sudo removal, he
uses `su -` via the recovered root password. Nothing to maintain,
no special access paths to audit.

**Env file is manually created and root-owned.**
`/opt/allowlist/env` contains `NEXTDNS_CONFIG_ID`. It is created
by the user during `manual_work.md` step 4-5, then immediately made
root-owned (`chown root:root`, `chmod 600`). The generate script
reads it as root — no security loophole.

**Allowlist.txt is root-owned; add/remove use sudo.**
All `add` and `remove` operations pipe through `sudo tee` or
`sudo sh -c`, since the allowlist file is root-owned. The
empty-list guard on `lock` prevents accidental full-block.

**Seal runs as root, outputs to mike's home.**
`seal` runs via `sudo /opt/allowlist/allowlist.sh seal`. It
collects passwords, encrypts with `tle` (as root — bypasses
nftables to reach drand), and writes `sealed-credentials` to
`~mike/.config/` owned by `mike:mike`, mode 644. mike can read
it for podman recovery but cannot modify the lock.

**Recovery uses podman, not host tools.**
After sudo removal, mike can't run `su -` without the root
password and can't decrypt the sealed file (no `su` access to
root). Recovery path:

1. Timelock expires
2. `podman run --rm -v ~/.config:/host-config:rw alpine sh -c "apk add -q curl tar && curl -fsSL -o /tmp/tlock.tar.gz https://github.com/drand/tlock/releases/download/v1.2.0/tlock_1.2.0_linux_amd64.tar.gz && tar xzf /tmp/tlock.tar.gz -C /usr/bin tle && /usr/bin/tle -d -o /host-config/recovery-credentials /host-config/sealed-credentials"`
3. Container has its own network namespace — bypasses host nftables, reaches drand, decrypts credentials
4. Writes `root_password=...` to `~/.config/recovery-credentials`
5. `su -` with recovered root password → `/opt/allowlist/allowlist.sh lock/unlock/add/remove`

**tle installed via `go install`, not a system package.**
`tle` (`github.com/drand/tlock/cmd/tle`) is not packaged for
Debian. It's installed by `install.sh` via `go install` into
the user's `~/go/bin/`. `seal` checks both `~/go/bin/tle` and
`/usr/local/bin/tle`.

### The `seal` Subcommand

```
sudo /opt/allowlist/allowlist.sh seal

 1. VERIFY tle exists (check ~/go/bin/tle and /usr/local/bin/tle)
 2. VERIFY ~/.config/recovery-credentials exists (user creates it beforehand)
  3. PICK duration (30m / 1h / 3h / 1d / 3d / 7d / custom)
 4. ENCRYPT: cp to temp → tle -D <dur> --armor -o sealed-credentials → shred temp
 5. DELETE: shred -u ~/.config/recovery-credentials
 6. CHOWN sealed-credentials to mike:mike, chmod 644
 7. WIPE mike's bash history
 8. LOCK system (deploy browser policies + nftables DNS block)
 9. PRINT recovery instructions and reboot reminder
```

### Post-sudo-removal Operations

After `sudo gpasswd -d mike sudo`, mike can only run commands
that don't require privilege escalation:

- Any user-level command (browsers, editors, terminals)
- `podman run` (rootless, no polkit dependency)
- Everything under `/opt/allowlist/` is inaccessible

If mike needs to unlock (e.g., to add a domain), the recovery
path above is the only way.

### Files

| File | Purpose |
|---|---|
| `/opt/allowlist/allowlist.sh` | CLI — lock, unlock, toggle, status, add, remove, search, list, verify, seal |
| `/opt/allowlist/allowlist.txt` | Domain allowlist (root-owned) |
| `/opt/allowlist/mode` | Current mode state (locked / unrestricted) |
| `/opt/allowlist/generate-dnsmasq.sh` | dnsmasq config generator (reads allowlist.txt + mode) |
| `/opt/allowlist/generate-policies.sh` | Browser policy deployer |
| `/opt/allowlist/generate-nftables.sh` | nftables ruleset deployer |
| `/opt/allowlist/verify.sh` | 9-check verification |
| `~/go/bin/tle` or `/usr/local/bin/tle` | Time-lock encryption binary |
| `~mike/.config/sealed-credentials` | Timelock-encrypted recovery credentials (created by `seal`) |
