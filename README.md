# linux_setup

Automated provisioning and lockdown management for a Sway-based Wayland workstation. Deploys dotfiles, dev tools, and a DNS/firewall allowlist system that enforces a timed security policy with timelock-sealed credentials.

## Quick start

```bash
# 1. Fill in identity values
nano config.env

# 2. Deploy dotfiles
make dotfiles

# 3. Deploy dev configs
make dev
```

## Keybindings

**Core**
- `$mod+Return` — Open terminal
- `$mod+d` — Toggle application launcher
- `$mod+Shift+q` — Close focused window
- `$mod+Shift+e` — Exit Sway session
- `$mod+Shift+c` — Reload Sway configuration

**System**
- `$mod+v` — Toggle clipboard history window (CopyQ)
- `$mod+Shift+v` — Clear all clipboard history
- `$mod+n` — Toggle Wi-Fi connection panel
- `$mod+Shift+g` — Launch minimal browser (Brave)

**Windows & workspaces**
- `$mod+Escape` — Cycle to next workspace
- `$mod+Ctrl+n` — Create and jump to next available workspace
- `$mod+Arrow` — Move focus in direction
- `$mod+Shift+Arrow` — Move window in direction
- `$mod+1`–`$mod+5` — Switch to workspace 1–5
- `$mod+Shift+1`–`$mod+Shift+5` — Move window to workspace 1–5
- `$mod+f` — Toggle fullscreen
- `$mod+h` / `$mod+g` — Horizontal / vertical split

**Hardware**
- `Brightness ↑/↓` — Increase / decrease brightness
- `Volume ↑/↓` — Increase / decrease volume
