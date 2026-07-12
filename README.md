# linux_setup

Automated provisioning and lockdown management for a Sway-based Wayland workstation. Deploys dotfiles, dev tools, and a DNS/firewall allowlist system that enforces a timed security policy with timelock-sealed credentials.

## Quick start

```bash
# 1. Generate config.env (auto-detects system values)
tools/bootstrap-config.sh

# 2. Review and confirm values
nano config.env

# 3. Create GitHub credentials file
cp dev/github.env.template dev/github.env
nano dev/github.env  # fill in GITHUB_TOKEN, GIT_USER_NAME, GIT_USER_EMAIL

# 4. Run full install
./install.sh
```

`bootstrap-config.sh` copies `config.env.template` and fills in detected values. Check `config.env` before running — some paths (like `OBSIDIAN_VAULT_PATH`) may need manual adjustment.

## Adding a new package

1. Add a line to the appropriate inventory file in `packages/`:
   - `packages/apt.txt` — apt packages (one per line)
   - `packages/github_deb.txt` — GitHub .deb releases (pipe-delimited)
   - `packages/github_binary.txt` — GitHub standalone binaries
   - `packages/go_installs.txt` — Go tools
   - `packages/cargo_builds.txt` — Cargo/Rust tools
   - `packages/curl_scripts.txt` — curl-piped-to-bash scripts
2. Run `bash lib/20-packages.sh` to install

No code changes needed — just a line in a text file.

## Updating dotfiles or dev configs

After changing files in `dotfiles/` or `dev/`:

```bash
make all
```

This re-deploys all dotfiles and dev configs to `~/.config/`.

## Updating lockdown configs

After changing files in `lockdown/`:

```bash
sudo bash lib/60-lockdown.sh
```

## Re-running individual modules

Each module is standalone and can be run independently:

```bash
bash lib/30-hardware.sh       # re-run hardware config
bash lib/40-system_config.sh  # re-run system config
bash lib/50-github_setup.sh   # re-run GitHub setup
sudo bash lib/60-lockdown.sh  # re-run system lockdown
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
