# Network Architecture

## Two modes

| Mode | dnsmasq config | nftables rules | Effect |
|---|---|---|---|
| **unrestricted** | Forward all queries to 1.1.1.1 | Empty skeleton (accept all) | No filtering |
| **locked** | Only forward allowlisted domains | `skuid 1000 dport {53,853} drop` | Host DNS filtered; container traffic unaffected |

## Traffic flows

### Host DNS (locked)

```
app (UID 1000)
  → dnsmasq (127.0.0.1:53, loopback allowed by nftables)
  → domain in allowlist? → forward to 1.1.1.1 → response
  → domain NOT in allowlist? → NXDOMAIN (no upstream configured)
```

If app bypasses dnsmasq and queries an external DNS directly (e.g. `dig @1.1.1.1`):
```
app (UID 1000)
  → UDP dport 53 → nftables OUTPUT hook → skuid match → DROP
```

### Container DNS (locked or unrestricted)

```
container process (e.g. apk, pip, npm)
  → container's /etc/resolv.conf → 1.1.1.1 (set by containers.conf)
  → UDP dport 53 → pasta tap device → host netns FORWARD hook (accept)
  → internet
```

Container DNS is NOT affected by locked mode because:
- UDP packets transit via FORWARD hook (policy accept), not OUTPUT hook
- TCP traffic (port 443 for API/registry calls) matches no drop rule

### Container image pull (podman pull)

```
podman pull python:3-alpine
  → host process (UID 1000)
  → host DNS → dnsmasq (127.0.0.1:53)
  → resolve registry-1.docker.io → allowlisted → OK
  → HTTPS to registry-1.docker.io:443 → nftables: no drop on port 443 → OK
  → layer download from production.cloudflare.docker.com:443 → OK
```

Pull is a **host process**, so it must resolve via dnsmasq — this is why Docker Hub
domains are in the allowlist. Once cached, `podman run --rm` uses the local image
and inside-container traffic follows the container DNS path above.

## Component roles

| Component | Role | Why it exists |
|---|---|---|
| **dnsmasq** | Local DNS proxy; enforces allowlist when locked | Only allowlisted domains resolve for host processes when locked |
| **nftables OUTPUT** | Blocks host DNS leaks (`skuid 1000 dport {53,853} drop`) | Prevents `dig`, `curl`, or any app from bypassing dnsmasq |
| **nftables FORWARD** | Default accept | Container traffic transits here, not OUTPUT — intentional |
| **containers.conf** (`dns_servers = ["1.1.1.1"]`) | Sets container DNS to external resolver | Without it, containers inherit 127.0.0.1 (dnsmasq) and are subject to allowlist |
| **Docker Hub allowlist entries** | Allow `podman pull` to resolve when locked | Pull runs on host, must go through dnsmasq |
| **allowlist.txt** | Domain whitelist for locked mode | Single source of truth for what host processes can resolve |

## Internet Network Namespace (`internet-netns`)

A persistent network namespace that bypasses locked-mode DNS restrictions for
pre-approved tools (opencode, podman pull).

### Architecture

```
default netns                          internet-netns
┌──────────────────────┐              ┌──────────────────────┐
│  locked mode:         │              │  no restrictions     │
│  OUTPUT drop UID 1000 │              │  empty nftables      │
│  :53                  │              │                      │
│  dnsmasq (allowlist)  │              │  DNS: 1.1.1.1        │
│                       │              │                      │
│  veth-inet-host       │◄───veth─────▶ veth-inet-ns         │
│  10.0.4.1/30          │              │  10.0.4.2/30         │
│                       │              │                      │
│  FORWARD (accept)     │              │  opencode,           │
│  NAT (masquerade)     │              │  podman pull         │
│  10.0.4.0/30          │              │                      │
│                       │              │                      │
│  wlan0 ──▶ internet                 │                      │
└──────────────────────┘              └──────────────────────┘
```

### Packet flow (e.g. internet-opencode)

```
internet-opencode
  → sudo → root runs enter-internet-netns
  → wrapper validates binary path → logs to syslog
  → ip netns exec internet-netns sudo -u mike opencode ...
  → opencode resolves via 1.1.1.1:53 (direct, no dnsmasq)
  → packet: internet-netns OUTPUT (empty) → veth-inet-ns
  → veth-inet-host → default-ns FORWARD (accept)
  → POSTROUTING masquerade (10.0.4.2 → host IP) → wlan0 → internet
```

### NAT rule (added to both base and locked nftables templates)

```nft
table inet nat {
        chain postrouting {
                type nat hook postrouting priority srcnat; policy accept;
                ip saddr 10.0.4.0/30 masquerade
        }
}
```

This rule lives in both `nftables.conf.base` and `nftables.conf.locked`. It
only activates for traffic sourced from the veth subnet. Host and container
traffic are unaffected.

### Entry wrapper

`/usr/local/bin/enter-internet-netns` (root:root 755) validates the binary path
via `readlink -f` + `command -v` fallback, then runs it inside the namespace.
Only pre-approved binaries are allowed:

| Binary | Restriction |
|---|---|
| `/home/mike/.opencode/bin/opencode` | Full binary, any args |
| `/usr/bin/podman pull` | Only the `pull` subcommand |

Filesystem, IPC, and user namespace are fully shared with the host — the
wrapper is the sole access gate.

### Verification

| Command | Expected result |
|---|---|
| `internet-opencode --model "test"` | Resolves domains outside the allowlist |
| `internet-podman-pull alpine` | Pulls image successfully when locked |
| `internet-podman-run alpine` | Denied by wrapper (only `pull` allowed) |
| `sudo enter-internet-netns bash` | Denied by wrapper (not in allowlist) |

## Verify test behavior

| Test | What it does | Host or container | Why it works when locked |
|---|---|---|---|
| 7a | `apk update` in alpine | Container | Container DNS → 1.1.1.1, alpine already cached |
| 7b | `pip install six` in python:3-alpine | Container (image pull: host) | Image pull resolves via allowlisted Docker Hub; pip inside uses container DNS |
| 7c | `npm install left-pad` in node:alpine | Container (image pull: host) | Same as 7b |
