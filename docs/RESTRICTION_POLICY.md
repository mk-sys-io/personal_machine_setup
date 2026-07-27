# Restriction Policy

## Overview

This document explains the technical rationale behind the restriction model
implemented by the Ark system. These restrictions are not arbitrary — they
are the foundation of the defense-in-depth strategy that makes distraction
blocking effective.

The core principle: **normal daily operations do not require sudo**. The
restrictions exist because possessing sudo powers allows the user to alter
system components, rendering the entire defense-in-depth strategy pointless.

---

## Sudo Removal

### Why Sudo Is Removed

After `ark lock`, the user loses sudo group membership. This is the primary
defense against circumvention of the distraction blocking system.

### What Sudo Removal Prevents

- **VPN installation** — `sudo apt install wireguard` — cannot install system packages
- **Alternative browser** — `sudo apt install firefox` — cannot install packages that ignore browser policies
- **Proxy tools** — `sudo apt install privoxy` — cannot install routing tools
- **System configuration** — `sudo vim /etc/dnsmasq.conf` — cannot modify DNS configuration
- **Blocklist modification** — `sudo blocklist add ...` — cannot run blocklist manager (no sudo group)
- **Service manipulation** — `sudo systemctl stop dnsmasq` — cannot stop filtering service

### The Threat Model

Without sudo removal, the user could:
1. Install a VPN that routes all traffic around DNS-level blocking
2. Install an alternative browser that ignores enterprise policies
3. Modify DNS configuration to bypass dnsmasq filtering
4. Stop the filtering service entirely
5. Modify the blocklist to remove distractions

Each of these bypasses would defeat the purpose of the distraction blocking
system. Sudo removal is the only logical defense against VPN bypass — the
most obvious and effective circumvention method.

---

## Package Management

### Why Only APT Is Allowed

The system allows `apt` via NOPASSWD sudoers rules for essential operations:
- `apt update` / `apt upgrade` — system maintenance
- `apt install --reinstall *` — reinstalling existing packages

These are safe because:
1. They only affect packages already installed on the system
2. They cannot install new packages that could be used for circumvention
3. They are read-only operations (except reinstall, which is limited)

### Why Flatpak, Snap, and Nix Are Blocked

These package managers can install user-space applications without root
privileges, creating bypass vectors:

- **Flatpak** — user-space app installation — can install alternative browsers, VPN clients
- **Snap** — user-space app installation — can install alternative browsers, VPN clients
- **Nix** — user-space package manager — can install any software without sudo

If these are present on the system, they could be used to:
- Install a VPN client that bypasses DNS-level blocking
- Install an alternative browser that ignores enterprise policies
- Install proxy tools that route around the blocklist

### Ark Check

Ark should verify that flatpak, snap, and nix are not installed before
enabling focused or locked mode. If present, warn the user that these tools
can be used to circumvent the distraction blocking system.

This check should be added to the pre-flight checks in `ark-plan.md`.

---

## Development Workflow

### Podman as the Approved Path

Development needs are answered by podman, not docker. The reasons are
technical and deliberate:

1. **Docker requires sudo or polkit authorization** — the Docker daemon
   runs as root, and connecting to it requires elevated privileges. This
   creates a bypass vector if the user can manipulate Docker.

2. **Podman runs rootless by default** — the daemon (if any) runs as the
   user, and containers run without root privileges. This is safe because
   container operations don't require system-level access.

3. **Existing alias** — `docker` is aliased to `podman` in the shell
   configuration, providing compatibility without the security risks.

### Container Isolation

Development inside containers is intentionally safe because:

1. **Package installation is isolated** — `pip install`, `npm install`,
   `cargo install`, and `go install` all install to user-space directories
   inside the container. These do not affect the host system.

2. **GUI apps are notoriously hard to run inside containers** — running
   graphical applications requires X11/Wayland socket forwarding, Xauthority
   configuration, GPU passthrough, and complex display server integration.
   This is not mainstream and requires significant setup.

3. **A VPN inside a container does not affect the live system** — containers
   have their own network namespace. Traffic routing inside the container
   is isolated from the host network stack. A VPN running inside a podman
   container cannot route host traffic through the VPN.

4. **Container images are reproducible** — development environments can be
   declared in Containerfiles, ensuring consistency across sessions.

### Why This Is Safe

The combination of these factors means:
- Developers can install any packages they need (pip, npm, cargo, go)
- These packages are isolated to the container environment
- No system-level changes are possible from within the container
- The host system remains uncompromised

---

## Browser Extension Blocking

### Why Extensions Are Blocked

Browser extensions are blocked via enterprise policies to prevent:

1. **VPN extensions** — extensions like NordVPN, ExpressVPN, or ProtonVPN
   can route browser traffic through VPN tunnels, bypassing DNS-level
   blocking entirely.

2. **Proxy extensions** — extensions like Proxy SwitchyOmega or FoxyProxy
   can route traffic through proxy servers, circumventing the blocklist.

3. **Ad blocker bypasses** — some extensions can modify DNS behavior or
   intercept requests before they reach the system resolver.

### Enterprise Policy Implementation

The browser policy (`policy.json.template`) enforces:
- `"installation_mode": "blocked"` — blocks all extension installation
- `"blocked_install_message": "Only administrator-approved extensions are permitted."` — clear error message
- Only administrator-approved extensions can be installed (via `ExtensionInstallForcelist`)

This prevents the user from installing any extension that could be used to
circumvent the distraction blocking system.

---

## Daily Use Cases

These restrictions do not impact normal daily operations:

- **Coding** — editors, compilers, interpreters, git, SSH — no sudo required
- **Documents** — LibreOffice, markdown editors, PDF viewers — no sudo required
- **Video editing** — Kdenlive, DaVinci Resolve, OBS — no sudo required
- **Building software** — make, cmake, cargo, npm, pip — no sudo required
- **Web browsing** — Firefox, Chrome, Brave — no sudo required
- **Email** — Thunderbird, webmail — no sudo required
- **Media playback** — VLC, mpv, music players — no sudo required

The restrictions only prevent:
- Installing new system packages (use Flatpak/AppImage/Nix instead)
- Modifying system configuration (rarely needed for daily use)
- Running `sudo blocklist` to modify the blocklist (by design)

After `ark lock`, the user is limited to pre-configured tools and user-space
applications. This is the intended behavior — the system enforces commitments
the user deliberately chose.

---

## Available in Locked Mode

Even after sudo group removal, certain diagnosis and troubleshooting tools
remain whitelisted via sudoers NOPASSWD rules. These are read-only or
recovery-focused operations that do not create bypass vectors:

- **System status** — `systemctl status`, `systemctl is-active`, `systemctl is-enabled`, `systemctl list-units`
- **Logs** — `journalctl`, `dmesg`
- **Firewall inspection** — `nft list` (read-only)
- **Network diagnostics** — `ip link set` (interface recovery)
- **WiFi radio** — `rfkill` (unblock after soft-block)
- **Service recovery** — `systemctl restart NetworkManager`, `systemctl restart dnsmasq`, `systemctl restart nftables`
- **System control** — `systemctl reboot`, `systemctl poweroff`, `systemctl suspend`
- **Timeshift** — `timeshift` (for snapshot/restore via ark)
- **Immutable flag** — `chattr` (for sealed credentials)
- **Clipboard** — `wl-copy`, `cliphist` (run as self)
- **Internet namespace** — `enter-internet-netns` (approved commands only)
- **Power profiles** — `powerprofilesctl set`

These tools are safe because they:
1. Cannot install new packages
2. Cannot modify system configuration
3. Cannot stop or start filtering services
4. Are either read-only or limited to specific recovery operations

---

## Emergency Recovery

### Recovery via Ark

If the user needs to revert the system after `ark lock`:

1. **`ark revert` subcommand** — reverts to a pre-lockdown timeshift snapshot
2. **Cooldown period** — must wait a configurable duration before revert is allowed
3. **Text copy challenge** — user must manually type a confirmation phrase to prove intent
4. **Timer display** — shows remaining cooldown time and prompts for confirmation

The revert process is designed to prevent impulsive rollback while allowing
legitimate recovery. The text copy challenge adds physical friction that
cannot be bypassed with a quick y/N.

### Recovery Timeline

- **Cooldown starts** — when `ark lock` is executed
- **Revert available** — after cooldown expires (configurable, default 72 hours)
- **Text challenge** — user must type a long confirmation phrase
- **Snapshot restore** — timeshift restores the pre-lockdown state
- **Reboot** — system reboots into unrestricted mode

This ensures the user has time to reflect on their decision and cannot
accidentally or impulsively revert the system.
