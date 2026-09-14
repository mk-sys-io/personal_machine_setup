# Ark Enhancement: OS-Integrated Distraction Blocking

Design document for shifting from a sudo-removal toggle model to
DNS-chokepoint-based enforcement. Personal reference — not a user guide.

## Problem Statement

The current Ark model is a toggle:

```
unrestricted ──[ark enable]──> focused (sudo removed)
focused ──[ark disable]──> unrestricted (sudo restored)
```

Sudo removal works, but the 81-line NOPASSWD whitelist in
`etc/ark/sudoers/99-mike-tools` is fragile. Each line is:

- A potential bypass vector if the command has unintended side effects
- A maintenance burden — every new tool requires a new rule
- Symptom-curing — rules are added when something breaks, removed when
  something is exploited

The fundamental issue: the system toggles a privilege switch (sudo on/off)
and then patches the holes that creates. The true solution is making
blocking mechanisms part of the OS itself — so they can't be toggled or
removed.

## Design Principle: Break What All Browsers Need

Instead of "block installation of every browser" (which requires catching
every browser name), target the single dependency all browsers share:
**DNS resolution**.

Every browser on Linux, regardless of which one, resolves domain names
through one of exactly three paths:

1. **System resolver** — glibc `getaddrinfo()` reads `/etc/resolv.conf`,
   queries the nameserver listed there (dnsmasq on 127.0.0.1)
2. **systemd-resolved** — `/etc/resolv.conf` is a symlink to 127.0.0.53,
   systemd-resolved handles forwarding
3. **Browser DoH** — DNS-over-HTTPS wraps DNS queries in TLS on port 443,
   bypassing the system entirely

If you control paths 1 and 2, and close path 3, no browser can resolve
domains — regardless of which browser it is, how it was installed, or
whether it respects enterprise policies.

This is the same insight Android uses (Private DNS at system level), Chrome
OS uses (verified boot + kernel root barrier), and iOS uses (Screen Time
managed by a separate Apple ID). The common pattern: **restrictions enforced
by a component the user doesn't control**.

## How Other OSes Solve This

### Android

- **Private DNS** (DNS-over-TLS) at system level — set once, travels with
  the device, works across all browsers and apps
- **Family Link** as a system app — can't be uninstalled, requires parent
  credentials to modify
- **App install approval** — new installs ping the parent's phone
- Key: DNS filtering is device-wide, not app-specific

### Chrome OS

- **Verified boot** — hardware root of trust ensures only signed code runs
- **Kernel root barrier** — root can't load untrusted kernel modules
  (restricted to modules from the verified root partition)
- **seccomp** — restricts what even root can do at the kernel level
- **Device policies** — enforced by the OS, not the user; can't be disabled
  without admin credentials
- Key: restrictions are architectural, not policy-based

### iOS

- **Screen Time** managed by a separate Apple ID — can't be removed without
  the parent's credentials
- **MDM profiles** — enterprise-managed, require admin to remove
- **App Library restrictions** — system-enforced, not user-configurable
- Key: restrictions live in a different trust domain than the user

### What We Can Adapt

None of these require hardware root of trust. The applicable patterns:
- **DNS as system-level chokepoint** (Android pattern) — our primary mechanism
- **Process confinement that survives privilege changes** (AppArmor, inspired
  by Chrome OS seccomp)
- **Configuration locked by a time-based mechanism** (TLE, already in Ark)

## The DNS Chokepoint

### Why DNS Is the Universal Dependency

Browsers fundamentally need to resolve domain names. They can:

| Resolution path | How it works | How we block it |
|---|---|---|
| System resolver | glibc reads `/etc/resolv.conf` → dnsmasq | resolv.conf immutable, points to 127.0.0.1 |
| systemd-resolved | 127.0.0.53 stub → forwarded upstream | dnsmasq is the upstream |
| DoH (DNS-over-HTTPS) | Browser connects to 1.1.1.1:443 directly | Enterprise policy disables DoH + nftables blocks endpoints |
| Hardcoded IP | `curl http://1.1.1.1` | nftables drops direct DNS; TCP to IPs allowed (residual) |

### Current Controls Already Deployed

| Control | What it does | Status |
|---|---|---|
| `/etc/resolv.conf` → `127.0.0.1` | All system DNS goes through dnsmasq | Deployed |
| `chattr +i /etc/resolv.conf` | User can't change the resolver | Deployed |
| `dns=none` in NetworkManager | NM doesn't overwrite resolv.conf | Deployed |
| dnsmasq blocklist config | Blocks distraction domains at DNS level | Deployed |
| nftables `dport 53 drop` | User can't make direct DNS queries | Deployed |
| nftables tun/tap/wg drop | User can't create VPN tunnels | Deployed |
| Browser enterprise policy | Disables DoH, blocks extensions, restricts downloads | Deployed |
| Polkit lockout | Blocks pkexec escalation | Deployed |

The system already covers paths 1, 2, and partially 3. The remaining gap
is DoH.

## The DoH Gap

DNS-over-HTTPS wraps DNS queries in TLS on port 443 — indistinguishable
from regular HTTPS traffic. Chrome, Firefox, and Brave can all use DoH,
which bypasses dnsmasq and nftables port-53 drops entirely.

### Three Approaches to Close It

**Approach 1: Block Known DoH Endpoints (nftables, immediate)**

Add nftables rules to drop TCP connections to known DoH providers:

```nft
# Block known DoH endpoints (port 443, specific IPs)
tcp dport 443 ip daddr { 1.1.1.1, 1.0.0.1, 8.8.8.8, 8.8.4.4,
  9.9.9.9, 149.112.112.112, 64.6.64.6, 64.6.65.6,
  185.228.168.9, 185.228.168.10 } drop
```

- Pros: Simple, immediate, catches 80% of DoH usage
- Cons: Arms race — new providers appear; can be bypassed with obscure ones
- Effort: Low — add rules to `nftables.conf.restricted`

**Approach 2: systemd-resolved Allowlist Mode (nuclear)**

Replace dnsmasq with systemd-resolved in allowlist-only mode — only listed
domains resolve, everything else returns NXDOMAIN.

- Pros: Native to systemd; no external dependency
- Cons: systemd-resolved doesn't natively support domain allowlists;
  would need a custom resolver or patch
- Effort: Very high — upstream modification

### Recommended: Approach 1 (immediate) + AppArmor confinement (long-term)

## Proposed Architecture

```
Layer 1: /etc/resolv.conf → 127.0.0.1 (dnsmasq)
  • immutable (chattr +i) — user can't change resolver
  • NetworkManager dns=none — NM can't overwrite
  • ALL system DNS goes through dnsmasq

Layer 2: dnsmasq blocklist
  • Blocks distraction domains at DNS level
  • Returns NXDOMAIN for blocked domains
  • Config file root:root, immutable

Layer 3: nftables
  • Drops direct DNS (port 53) from user
  • Drops tun/tap/wg interfaces (VPN)
  • Drops known DoH endpoints (NEW)

Layer 4: Browser enterprise policy
  • Disables DoH in Chrome/Brave
  • Forces system DNS usage
  • Blocks extensions, restricts downloads
```

Each layer catches what the others miss. No single layer is complete.
The strength is in the overlap — same philosophy as the existing 6-layer
VPN mitigation model (see [VPN Mitigation](VPN_MITIGATION.md)).

## eBPF/XDP DNS Filter (rejected)

A kernel-level eBPF/XDP program attached to the NIC driver was considered
to intercept DNS queries at the driver level and drop blocked domains
before they enter the kernel stack. It was rejected: the sheer complexity
(~300 lines of C, clang/llvm toolchain, kernel 4.18+/5.10+ constraints,
kernel-space crash risk, and it still cannot inspect TLS-encrypted DoH)
is not worth it just to protect against a single sudo-command bypass
(`systemctl stop dnsmasq`). The nftables DoH endpoint blocking (Layer 3)
plus AppArmor process confinement (below) covers the same threat with far
less risk and no new kernel component.

## AppArmor Process Confinement

### The Insight

AppArmor is a Linux Security Module (LSM) that enforces Mandatory Access
Control (MAC) through path-based profiles. Unlike sudo removal (which
toggles a group membership), AppArmor confinement is **process-level** — it
applies to specific binaries regardless of the user's privilege level.

Key property: **AppArmor policies survive sudo restoration.** Even if the
user gets sudo back, a confined process is still confined. The kernel
enforces the policy, not the user's group membership.

### Why This Complements Sudo Removal

| Aspect | Sudo removal | AppArmor |
|---|---|---|
| What it constrains | User's group membership | Specific process capabilities |
| Survives sudo restore | No — user gets full root back | Yes — kernel enforces regardless |
| Granularity | Binary: user has sudo or doesn't | Per-binary: each app has its own profile |
| Maintenance | NOPASSWD rules per command | Profile per application |
| Enforcement | User-space (sudoers check) | Kernel (LSM hook) |

### Proposed Profile for the User

```profile
#include <tunables/global>

user工作站 {
  # Can't install new packages (only reinstall existing)
  /usr/bin/apt install --reinstall ** r,
  deny /usr/bin/apt install x,

  # Can't modify DNS
  deny /etc/dnsmasq.conf w,
  deny /etc/resolv.conf w,

  # Can't modify firewall
  deny /etc/nftables.conf w,
  deny /usr/sbin/nft write,

  # Can't load kernel modules
  deny /sbin/modprobe x,
  deny /sbin/insmod x,

  # Can't stop filtering services
  deny /usr/bin/systemctl stop dnsmasq x,
  deny /usr/bin/systemctl stop nftables x,
  deny /usr/bin/systemctl stop internet-netns x,

  # CAN read logs (safe)
  /var/log/** r,
  /run/log/** r,
  /var/log/journal/** r,

  # CAN reboot/poweroff/suspend (safe)
  /usr/bin/systemctl reboot x,
  /usr/bin/systemctl poweroff x,
  /usr/bin/systemctl suspend x,
}
```

### Key Difference from Current Model

Current: "user has no sudo → can't run `apt install`"
Proposed: "user's `apt` process is confined → can only reinstall existing
packages" — regardless of whether the user has sudo.

This means the user could potentially **keep sudo** for convenience
(reboot, package reinstall, service restart) while still preventing
bypass — because confinement is at the process level, not the privilege
level.

### Deployment Considerations

- AppArmor is Debian/Ubuntu's default MAC (simpler than SELinux's
  label-based approach)
- Profiles are path-based text files in `/etc/apparmor.d/`
- `aa-genprof` generates profiles interactively; `aa-logprof` tunes them
- Profiles can run in complain mode (log violations) before enforcing
- Risk: misconfigured profiles can break legitimate operations; test in
  complain mode first

## Revised Sudo Model

### What Gets Removed from NOPASSWD Rules

| Rule | Current | Proposed | Rationale |
|---|---|---|---|
| `apt install --reinstall *` | Allowed | Remove | AppArmor confines apt; DNS chokepoint blocks package downloads |
| `systemctl restart dnsmasq` | Allowed | Remove | dnsmasq runs as root; user shouldn't restart filtering service |
| `systemctl restart nftables` | Allowed | Remove | nftables runs as root; user shouldn't restart firewall |
| `systemctl restart NetworkManager` | Allowed | Keep | WiFi recovery is legitimate; NM is not a bypass vector |
| `ip link set *` | Allowed | Keep | Interface recovery (WiFi stuck after suspend) is legitimate |
| `nft list *` | Allowed | Keep | Read-only; diagnostic value |
| `netmgr exec-grant *` | Allowed | Keep | Gated by `require_unrestricted()`; frozen in focused/locked |
| `tools deployment` | Allowed | Remove | Could deploy arbitrary binaries to /usr/local/bin |

### What Stays

These NOPASSWD rules survive because they're read-only or recovery-focused:

- Read-only diagnostics: `systemctl status/is-active/is-enabled/list-units`
- Logs: `journalctl`, `dmesg`
- System control: `reboot`, `poweroff`, `suspend`
- Network recovery: `systemctl restart NetworkManager`
- Interface recovery: `ip link set`, `rfkill`
- Firewall inspection: `nft list`
- Immutable flag: `immutable.sh` (whitelist wrapper)
- Clipboard: `wl-copy`, `clipse` (run as self)
- Internet namespace: `netmgr namespace exec`
- Ark CLI: `/usr/local/bin/ark`, `mcask`, `uncask`
- Power profiles: `powerprofilesctl set`

### Net Reduction

Current: ~25 NOPASSWD rules
Proposed: ~17 NOPASSWD rules
Removed: ~8 rules that create potential bypass vectors

Each removed rule is safe because the DNS chokepoint + AppArmor profile
covers the bypass vector that the rule was trying to prevent.

## Refactoring the Sudo Whitelist

The 81-line NOPASSWD whitelist in `etc/ark/sudoers/99-mike-tools` is a
flat list of per-command grants. It works, but it is hard to audit at a
glance and every new tool adds another line. The high-level concept is to
split it into themed `sudoers.d/` files, each grouping related commands
under a `Cmnd_Alias` so the grants read as coherent sets rather than an
undifferentiated wall of rules.

`Cmnd_Alias` is sudoers' built-in mechanism for naming a group of
commands and referencing the whole group in one rule:

```sudoers
Cmnd_Alias DIAG = /usr/bin/systemctl status *, /usr/bin/systemctl is-active *, \
                  /usr/bin/systemctl is-enabled *, /usr/bin/systemctl list-units *, \
                  /usr/bin/journalctl *, /usr/bin/dmesg

Cmnd_Alias POWER = /usr/bin/systemctl reboot, /sbin/reboot, \
                   /usr/bin/systemctl poweroff, /usr/bin/systemctl suspend

{{ .Env.USERNAME }} ALL=(root) NOPASSWD: DIAG, POWER
```

The `@includedir /etc/sudoers.d` directive loads files in lexical order,
so zero-padded prefixes give each theme a stable slot:

```
/etc/sudoers.d/00-ark-diagnostics   # read-only: status, is-active, logs, dmesg
/etc/sudoers.d/10-ark-system        # reboot, poweroff, suspend
/etc/sudoers.d/20-ark-network       # NM/dnsmasq/nftables restart, ip link, rfkill, nft list
/etc/sudoers.d/30-ark-ark           # ark, mcask, uncask, immutable.sh
/etc/sudoers.d/40-ark-netmgr        # netmgr namespace, exec-grant, allowlist, status, search
/etc/sudoers.d/50-ark-misc          # powerprofilesctl, wl-copy, clipse, tools install
```

The benefit is auditability: reviewing `20-ark-network` shows the whole
network-recovery surface in one place, making it obvious that
`systemctl restart dnsmasq` sits alongside the other network grants and
can be dropped once AppArmor confinement makes it unnecessary.

One limitation: `Cmnd_Alias` only aliases commands whose wildcard is the
sole final argument. Rules like `apt install --reinstall *` and
`install -D -m 755 .../tools/* .../bin/*` have wildcards in non-final
positions and cannot be aliased — they must stay as individual rules
(or be wrapped). The refactor is therefore a reorganization, not a
reduction in grants.

## Residual Vectors (Accepted)

These attack paths bypass all layers, including the proposed enhancements.
They are accepted as out of scope for the self-control threat model
because they require deliberate premeditation.

1. **Userspace proxies** — `ssh -D <port> <raw_ip>`, `tor+torsocks`,
   `shadowsocks-local`, `sing-box` in system-proxy mode. These run as the
   unprivileged user (no root needed), use plain TCP sockets (no tun
   interface to block), and connect to raw IPs (no DNS to filter). The
   restricted nftables config accepts all TCP egress except tunnel
   interfaces and direct DNS. Closing this requires egress allowlisting,
   which is disproportionate for a personal system.

2. **`curl | sh` via raw IP** — If the user hardcodes a CDN IP address,
   the install script bypasses DNS. Most VPN `curl | sh` scripts require
   sudo to install to `/usr/bin/`, so sudo removal catches this in
   practice. A script installing only to `~/.local/bin` would work, but
   the binary is caught by the artifact gate at next enable.

3. **Renamed binaries** — A VPN binary renamed to a non-matching name
   (e.g., `work` instead of `wg`) evades the filename-based artifact
   gate. Detection by ELF-header scanning is disproportionate for the
   threat model.

The system is "friction, not a wall": it prevents impulsive bypass;
deliberate circumvention requires leaving evidence in the user's own
review ritual (`ark status`). See [Restriction Policy](RESTRICTION_POLICY.md)
for the full rationale.

## Implementation Roadmap

### Phase 1: DoH Endpoint Blocking (immediate, low effort)

**What:** Add nftables rules to `nftables.conf.restricted` that drop TCP
connections to known DoH provider IPs on port 443.

**Where:** `etc/ark/nftables/nftables.conf.restricted` — add a new rule
after the tun/tap/wg drops.

**Effort:** ~10 lines of nftables rules. No new components.

**Risk:** Low — only blocks specific IPs, not port 443 broadly. Legitimate
HTTPS to non-DoH servers is unaffected.

**Limitation:** Arms race — new DoH providers appear. Mitigated by the
browser enterprise policy (Layer 4) which disables DoH at the browser
level.

### Phase 2: AppArmor Profiles (medium effort, high value)

**What:** Create an AppArmor profile for the user that confines dangerous
operations (apt install, modprobe, nft write, systemctl stop) at the
process level.

**Where:** New file `etc/ark/apparmor/user工作站` deployed to
`/etc/apparmor.d/` by `lib/60-ark.sh`.

**Effort:** ~50 lines of AppArmor profile + ~20 lines of deployment code
in `60-ark.sh`. Profile needs testing in complain mode first.

**Risk:** Medium — misconfigured profiles can break legitimate operations.
Start in complain mode, review logs, then switch to enforce.

**Value:** Process-level confinement that survives sudo restoration. This
is the key architectural improvement — blocking is enforced by the kernel,
not by group membership.

### Phase 3: sudoers.d/ Refactor (low effort, high auditability)

**What:** Split the 81-line `99-mike-tools` whitelist into themed
`sudoers.d/` files, each grouping related commands under a `Cmnd_Alias`
(see "Refactoring the Sudo Whitelist" above).

**Where:** Replace `etc/ark/sudoers/99-mike-tools` with a set of
`etc/ark/sudoers.d/NN-ark-*.conf` files deployed to `/etc/sudoers.d/`.

**Effort:** Reorganization only — no grant reduction. The wildcard-heavy
rules (`apt install --reinstall *`, `install -D ...`) stay as individual
rules since `Cmnd_Alias` cannot alias them.

**Risk:** Low — purely a layout change; `visudo -c` validation in
`60-ark.sh` already covers the new files.

**Value:** Makes the network-recovery surface (`20-ark-network`) visible
in one place, so `systemctl restart dnsmasq` can be audited and dropped
once AppArmor confinement (Phase 2) makes it unnecessary.

### Dependency Order

```
Phase 1 (nftables DoH rules)
  ↓ no dependencies
Phase 2 (AppArmor profiles)
  ↓ depends on Phase 1 for full DoH coverage
Phase 3 (sudoers.d/ refactor)
  ↓ independent — can land before or after Phase 2
```

Phase 1 can be deployed immediately. Phase 2 and 3 are independent of
each other but both benefit from Phase 1 being in place first.
