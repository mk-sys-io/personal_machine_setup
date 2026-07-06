# Internet Network Namespace Plan

## Problem

In locked mode, host processes (UID 1000) are subject to:

1. **dnsmasq allowlist** — only allowlisted domains resolve for host processes
2. **nftables `skuid 1000 dport {53,853} drop`** — blocks direct DNS queries to external resolvers

This breaks any tool that needs to reach domains outside the curated allowlist:

- **opencode** — web search, web fetch, MCP tools, and anything requiring internet or DNS fails when locked
- **podman pull** — image pull fails (Docker Hub CDN domains get NXDOMAIN). Container traffic (`podman run`) already works via the FORWARD hook + `dns_servers = ["1.1.1.1"]`

## Solution

A persistent network namespace (`internet-netns`) with:

- Its own network stack (veth pair, private routing)
- Direct DNS resolution (`nameserver 1.1.1.1`)
- No nftables restrictions (empty nftables table inside namespace)
- A root-owned **command allowlist** entry wrapper as the sole gate
- Pre-configured at install time, immutable from locked state

## Core Design Property — Network Isolation Only

`internet-netns` uses `ip netns add` (a network namespace only):

| Resource | Isolated? | Consequence |
|---|---|---|
| Network stack (interfaces, routes, nftables, DNS) | **Yes** | Own IP, own routing, own resolver, no nftables UID drop |
| `/home/mike` — configs, SSH keys, repos, caches | **No** | Same filesystem, same UID 1000. Tools see all their normal files |
| `/run/user/1000/` — Wayland, D-Bus, PipeWire sockets | **No** | GUI apps render, audio plays, clipboard works — no bind-mounts needed |
| Process tree, `/proc`, `/sys` | **No** | Full visibility of host processes |
| Shell environment, PATH, home | **No** | Tool runs exactly as it would outside, just with different networking |

**Named constraint**: Filesystem and IPC are fully shared with the host. All access control is via binary+subcommand validation in the entry wrapper — there is no sandboxing beyond what the wrapper enforces.

## Architecture

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

### Packet flow

```
opencode (alias)
  → sudo → root runs enter-internet-netns
  → wrapper validates binary + subcommand → logs to syslog
  → ip netns exec internet-netns sudo -u mike opencode ...
  → opencode resolves via 1.1.1.1:53 (direct, no dnsmasq)
  → packet: internet-netns OUTPUT (empty) → veth-inet-ns
  → veth-inet-host → default-ns FORWARD (accept)
  → POSTROUTING masquerade (10.0.4.2 → host IP) → wlan0 → internet

Return path: reverse, conntrack handles DNAT.
OUTPUT drop rule (skuid 1000 dport 53) never matched — FORWARD, not OUTPUT.
```

## Wrapper: Command Allowlist

`/usr/local/bin/enter-internet-netns` (root:root, 755) — the sole entry point. It validates the binary path and restricts podman to the `pull` subcommand.

```bash
#!/bin/bash
# enter-internet-netns — run a pre-approved command inside the internet namespace
# Usage: enter-internet-netns <command> [args...]
set -euo pipefail

CMD=$(readlink -f "$1" 2>/dev/null || echo "$1")

case "$CMD" in
    /home/mike/.opencode/bin/opencode)
        logger -t internet-netns "OK: $SUDO_USER $*"
        exec ip netns exec internet-netns sudo -u "$SUDO_USER" "$@"
        ;;

    /usr/bin/podman)
        if [ "${2:-}" != "pull" ]; then
            echo "Error: Only 'podman pull' allowed in internet-netns"
            logger -t internet-netns "DENY: $SUDO_USER podman ${2:-}"
            exit 1
        fi
        logger -t internet-netns "OK: $SUDO_USER $*"
        exec ip netns exec internet-netns sudo -u "$SUDO_USER" "$@"
        ;;

    *)
        echo "Error: Command not permitted in internet-netns"
        echo "Allowed commands:"
        echo "  /home/mike/.opencode/bin/opencode"
        echo "  /usr/bin/podman pull"
        logger -t internet-netns "DENY: $SUDO_USER $*"
        exit 1
        ;;
esac
```

**Adding new allowed commands** must be done in **unlocked state** (root has sudo), by editing this root-owned file. From locked state, the allowlist is frozen.

## New Files (in repo)

| Repo path | Deployed to | Mode | Purpose |
|---|---|---|---|
| `.config/scripts/setup-internet-netns.sh` | `/usr/local/lib/setup-internet-netns.sh` | root:root 755 | Creates netns, veth pair, routing, loopback |
| `.config/scripts/enter-internet-netns` | `/usr/local/bin/enter-internet-netns` | root:root 755 | Wrapper script with command allowlist |
| `.config/systemd/internet-netns.service` | `/etc/systemd/system/internet-netns.service` | root:root 644 | Oneshot service, creates namespace at boot |
| `.config/resolv/internet-netns.resolv.conf` | `/etc/netns/internet-netns/resolv.conf` | root:root 644 | `nameserver 1.1.1.1` |

## Modifications to Existing Files (in repo)

| File | Change |
|---|---|
| `.config/nftables/nftables.conf.base` | Append `table inet nat` with masquerade for `10.0.4.0/30` |
| `.config/nftables/nftables.conf.locked` | Append same NAT table |
| `.config/sudoers/99-mike-tools` | Add: `mike ALL=(root) NOPASSWD: /usr/local/bin/enter-internet-netns *` |
| `.config/bashrc` | Add aliases |
| `install.sh` | Deploy all new files; `systemctl enable --now internet-netns.service` |
| `docs/network_architecture.md` | Add namespace section and traffic flow |

### nftables nat table (added to both templates)

```nft
table inet nat {
        chain postrouting {
                type nat hook postrouting priority srcnat; policy accept;
                ip saddr 10.0.4.0/30 masquerade
        }
}
```

### bashrc aliases

```bash
alias opencode='sudo enter-internet-netns /home/mike/.opencode/bin/opencode'
alias podman-pull='sudo enter-internet-netns /usr/bin/podman pull'
```

These expand to `sudo enter-internet-netns <binary> [args...]`. All positional args, flags, and subcommands pass through — the alias is simple text substitution. Pipes and redirections apply to the calling shell, not inside the namespace (which is fine — both sides see the same filesystem).

**Alias naming convention**: A binary allowed in the wrapper without subcommand restriction gets a bare-name alias (`opencode` shadows the PATH binary). A binary restricted to a specific subcommand gets a `binary-subcommand` hyphenated alias (`podman-pull`), avoiding collision with the unrestricted PATH binary (`podman` for `podman run`).

### Sudo rule

```
mike ALL=(root) NOPASSWD: /usr/local/bin/enter-internet-netns *
```

The single `*` wildcard matches any arguments passed to the wrapper. Security relies on the wrapper's internal command allowlist — not on sudoers granularity.

## Protection Against Abuse

| What mike tries | Result | Mechanism |
|---|---|---|
| `opencode` | **Allowed** | opencode is in wrapper allowlist |
| `opencode --model x --flag y` | **Allowed** | `"$@"` passes all args; wrapper validates only the binary path |
| `podman-pull` | **Allowed** | Subcommand `pull` passes validation |
| `podman-pull registry.example.com/img` | **Allowed** | `"$@"` passes extra args; first check is for `pull` |
| `sudo enter-internet-netns /usr/bin/podman run -it alpine` | **Denied** | `$2` is `run`, not `pull` |
| `sudo enter-internet-netns firefox` | **Denied** | firefox not in wrapper allowlist |
| `sudo enter-internet-netns bash -c 'curl badsite.com'` | **Denied** | bash not in wrapper allowlist |
| `opencode` where opencode binary is replaced with a script | **Denied** | `readlink -f` resolves to modified file; wrapper catches via `readlink -f` path check |
| `cp /usr/bin/opencode /tmp/fake; opencode /tmp/fake` | **Denied** | `readlink -f` resolves to `/tmp/fake` — no case match |
| Modify `/usr/local/bin/enter-internet-netns` to add firefox | **Impossible** | File is root:root 755; mike has no sudo for arbitrary file edits |
| Remove aliases from `.bashrc` | **No bypass gained** | Can still invoke via full `sudo enter-internet-netns <binary>` — wrapper still validates |
| Create a new alias `internet-foo` → `sudo enter-internet-netns /some/other/binary` | **Denied** | Alias is a shell shortcut; wrapper validates the binary regardless |

## Edge Cases and Implicit Risks

### 1. Integrated Terminal / Command-Execution Escalation (Policy Risk)

Any tool that provides a built-in shell, command runner, or plugin system
spawns subprocesses that **inherit the namespace's unrestricted network**.

| Tool | Feature | Consequence |
|---|---|---|
| opencode | `!` prefix in TUI, agent `bash` tool, `/commands` with shell output | Any shell command run inside opencode has unfiltered internet |
| Future CLI tool | Integrated terminal, `:!` escapes, plugin exec | Same — subprocess inherits namespace |
| Future GUI app | Open dialog → file manager spawn, help → browser spawn | Child processes inherit namespace |

The wrapper validates the **entry point binary** but **cannot control what that
binary spawns**. This is inherent to the "network isolation only" design.

**Mitigation**: None in the namespace layer. This is a **policy acceptance**:
adding a binary to the wrapper allowlist means accepting that all of its
child processes also have unfiltered internet.

**For podman**: The wrapper restricts to `pull` only, which is an HTTP client
with no spawn capability. `podman run` (which spawns containers in nested
namespaces) is blocked and does not need the namespace — it works in locked
state without it (see item 3).

### 2. Wayland/GUI Environment Failures (Future-Proofing)

`ip netns exec` does not automatically propagate environment variables.
For GUI apps, `$WAYLAND_DISPLAY`, `$XDG_RUNTIME_DIR`, and
`$DBUS_SESSION_BUS_ADDRESS` must be explicitly preserved or the app will
crash with "Could not open display."

**Current status**: Not a problem — the allowlist has no GUI binaries.
opencode is a CLI TUI that communicates over stdio and does not touch
Wayland.

**If a GUI app is ever added to the wrapper**, the following pattern is
required:

```bash
ip netns exec internet-netns sudo -u "$SUDO_USER" \
    env WAYLAND_DISPLAY="$WAYLAND_DISPLAY" \
        XDG_RUNTIME_DIR="$XDG_RUNTIME_DIR" \
        DBUS_SESSION_BUS_ADDRESS="$DBUS_SESSION_BUS_ADDRESS" \
        "$@"
```

### 3. podman run Works Without the Namespace

`podman run` is blocked by the wrapper (only `pull` allowed), meaning there is
no alias for it — use `podman run` directly (PATH binary). This causes
**zero functionality loss** because:

- `podman run` runs rootless and creates its own container namespaces
- Container traffic goes through the nftables **FORWARD** hook (`policy accept`)
- The locked rule (`skuid 1000 dport {53,853} drop`) lives in **OUTPUT** and
  never sees container traffic
- `containers.conf` already sets `dns_servers = ["1.1.1.1"]` for containers

`podman run` works identically in locked and unlocked states, with or
without the namespace.

### 4. resolvconf / DHCP Race Conditions (Non-Issue)

`/etc/netns/<name>/resolv.conf` is a **kernel-level bind-mount** handled
by `ip netns exec`. When a process enters the namespace, the kernel binds
`/etc/netns/<name>/resolv.conf` over `/etc/resolv.conf` inside the namespace.
DHCP renewals and NetworkManager manipulations of the **host's**
`/etc/resolv.conf` do not touch the bind-mount target. The file at
`/etc/netns/internet-netns/resolv.conf` is never read by any host process.

The `chattr +i` hardening is unnecessary but harmless.

### 5. Abstract Socket / Unix Domain Socket Leaks (Non-Issue)

This concern assumes a trust boundary between "inside the namespace" and
"outside the namespace." **There is none.** Both sides run as the same user
(mike, UID 1000), on the same filesystem, with the same IPC visibility. A
process inside the namespace can already read and write everything mike can
access — the namespace adds **zero** filesystem, user, or process isolation.

There is nothing to "leak" because there is no compartment to leak from.

### 6. Evaluation Checklist for Future Wrapper Additions

Before adding any binary to the wrapper allowlist, assess:

1. **Does it provide a shell or command-execution feature?**
   (integrated terminal, `:!` escapes, plugin system, eval)
   → If yes, subprocesses inherit unrestricted internet (see item 1).

2. **Does it need display/Wayland/D-Bus?**
   → If yes, environment variables must be explicitly preserved (see item 2).

3. **Does it manage containers or namespaces?**
   → If yes, verify nested namespace operations work correctly. Some
   operations (e.g., `podman run`) fail inside a pre-existing network
   namespace and should be blocked by the wrapper.

4. **Does the functionality justify the policy risk?**
   → There must be a concrete need that cannot be met by adding domains to
   the existing allowlist. The namespace is an escape hatch, not a default
   path.

5. **Can it be restricted by subcommand?**
   → Like podman, only allow the specific subcommand that needs the namespace
   (e.g., `pull`) rather than the entire binary.

## Out of Scope (for this implementation)

- IPv6 — veth is IPv4 only
- Per-argument filtering beyond the podman subcommand check
- Traffic auditing inside the namespace (only entry logging via `logger`)
- User-facing sudo password prompt (existing `NOPASSWD` intent)
