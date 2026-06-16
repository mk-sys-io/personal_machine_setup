# Root Ownership Inventory

Every file below **must be root-owned** for the internet lockdown to hold.
If any of these become writable by user `mike`, the system can be bypassed.

---

## DNS Layer

| File | Owner | Perms | Immutable | Notes |
|------|-------|-------|-----------|-------|
| `/etc/resolv.conf` | root:root | 644 | `chattr +i` | Must point to `127.0.0.1` |
| `/etc/NetworkManager/conf.d/90-dns-none.conf` | root:root | 644 | — | Prevents NM from overwriting resolv.conf |
| `/etc/dnsmasq.conf` | root:root | 644 | — | dnsmasq daemon config |
| `/etc/dnsmasq.d/` | root:root | 755 | — | Config include directory |
| `/etc/dnsmasq.d/allowlist.conf` | root:root | 644 | — | Generated allowlist (by allowlist.sh) |

### Risk if writable
Change resolv.conf → upstream DNS → bypass. Stop dnsmasq → no DNS (denial, not bypass).

---

## Firewall Layer

| File | Owner | Perms | Immutable | Notes |
|------|-------|-------|-----------|-------|
| `/etc/nftables.conf` | root:root | 644 | — | nftables ruleset (DNS-leak block for UID 1000) |
| `/etc/systemd/system/sysinit.target.wants/nftables.service` | root:root | 644 | — | Auto-enable symlink |

### Risk if writable
Flush nftables rules → UID 1000 can reach port 53/853 directly → DNS leak.

---

## Browser Policy Layer

| File | Owner | Perms | Notes |
|------|-------|-------|-------|
| `/etc/brave/policies/managed/` | root:root | 755 | Policy directory |
| `/etc/brave/policies/managed/policy.json` | root:root | 644 | Disables DoH, geolocation, etc. |
| `/etc/opt/chrome/policies/managed/` | root:root | 755 | Policy directory |
| `/etc/opt/chrome/policies/managed/policy.json` | root:root | 644 | Same policies (Brave template) |
| `/etc/chromium/policies/managed/` | root:root | 755 | Policy directory |
| `/etc/chromium/policies/managed/policy.json` | root:root | 644 | Same policies |
| `/etc/firefox/policies/` | root:root | 755 | Policy directory |
| `/etc/firefox/policies/policies.json` | root:root | 644 | Disables TRR (DoH) |

### Risk if writable
Enable DoH → browser bypasses dnsmasq entirely → resolve any domain.

---

## PolicyKit Layer

| File | Owner | Perms | Notes |
|------|-------|-------|-------|
| `/etc/polkit-1/rules.d/99-internet-lockdown.rules` | root:polkitd | 644 | Blocks all pkexec actions for user mike |

### Risk if writable
Remove/modify rule → mike can use `pkexec` to run commands as root.

---

## Allowlist Utility

| File | Owner | Perms | Notes |
|------|-------|-------|-------|
| `/opt/allowlist/` | root:root | 750 | Scripts directory — no world access |
| `/opt/allowlist/allowlist.sh` | root:root | 750 | Lock/unlock/verify commands |
| `/opt/allowlist/allowlist.txt` | root:root | 640 | Domain allowlist |
| `/opt/allowlist/generate-dnsmasq.sh` | root:root | 750 | Generates dnsmasq config |
| `/opt/allowlist/generate-nftables.sh` | root:root | 750 | Generates nftables rules |
| `/opt/allowlist/generate-policies.sh` | root:root | 750 | Deploys browser policies |
| `/opt/allowlist/verify.sh` | root:root | 750 | DNS-leak verification |
| `/opt/allowlist/nftables.conf.base` | root:root | 640 | Base (no restrictions) |
| `/opt/allowlist/nftables.conf.locked` | root:root | 640 | Locked (DNS blocked) |
| `/opt/allowlist/brave-policy.json.template` | root:root | 640 | Policy template |
| `/opt/allowlist/firefox-policies.json.template` | root:root | 640 | Policy template |

### Risk if writable
Modify allowlist.txt → add `*` → allow all domains. Modify scripts → inject rules.

---

## Containers Layer

| File | Owner | Perms | Notes |
|------|-------|-------|-------|
| `/etc/containers/containers.conf` | root:root | 644 | Forces `dns_servers = ["1.1.1.1"]` |

### Risk if writable
Remove DNS restriction → containers inherit host dnsmasq → bypass.

---

## Cryptography / Recovery

| File | Owner | Perms | Notes |
|------|-------|-------|-------|
| `/usr/local/bin/tle` | root:root | 755 | Time-lock encryption binary. 755 = world-executable — safe because decryption is time-bound; mike can run it but cannot decrypt before the timelock expires. |
| `/usr/local/bin/unseal` | root:root | 755 | World-executable `tle -d` wrapper for credential recovery. |

---

## System Config (secondary risk)

| File | Owner | Perms | Notes |
|------|-------|-------|-------|
| `/etc/nsswitch.conf` | root:root | 644 | Could reorder host resolution (e.g. `mdns` before `dns`) |
| `/etc/hosts` | root:root | 644 | Could add static entries |
| `/etc/environment` | root:root | 644 | Could set `http_proxy` — but proxy needs network access |
| `/etc/apt/sources.list.d/brave-browser-release.list` | root:root | 644 | APT repo (low risk for bypass) |
| `/etc/apt/sources.list.d/google-chrome.list` | root:root | 644 | APT repo (low risk for bypass) |

---

## Services (can stop only with root)

| Service | Effect if stopped |
|---------|------------------|
| `nftables` | Removes DNS-leak firewall — mike can reach port 53/853 |
| `dnsmasq` | DNS breaks — resolv.conf still `127.0.0.1`, nothing listening (denial only) |
| `polkit` | Removes pkexec block — but polkit rules are persistent on disk |
| `NetworkManager` | No immediate effect with `dns=none` conf |

---

## What Works Without Root (for troubleshooting)

These tools work without any privilege escalation:
- `ping`, `dig`, `nslookup`, `host`, `curl`, `wget`
- `ip addr`, `ip route`, `ss`, `traceroute`
- `glow`, `python3`, `git`
- `systemctl --user status ...`
- `/usr/local/bin/unseal` — decrypt sealed credentials (world-executable, drand whitelisted)
- `/usr/local/bin/tle -d` (recovery — drand endpoints whitelisted in allowlist)
