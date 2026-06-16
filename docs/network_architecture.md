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

## Verify test behavior

| Test | What it does | Host or container | Why it works when locked |
|---|---|---|---|
| 7a | `apk update` in alpine | Container | Container DNS → 1.1.1.1, alpine already cached |
| 7b | `pip install six` in python:3-alpine | Container (image pull: host) | Image pull resolves via allowlisted Docker Hub; pip inside uses container DNS |
| 7c | `npm install left-pad` in node:alpine | Container (image pull: host) | Same as 7b |
