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

## Wallpaper setup

The default theme (GitHub Dark) ships with a wallpaper. Other themes require
you to provide your own.

### Adding a wallpaper

1. Place the image in the sway wallpaper directory:

```bash
mkdir -p ~/.config/sway/wallpaper
cp /path/to/your/wallpaper.jpg ~/.config/sway/wallpaper/
```

2. Update the `output * bg` line in `~/.config/sway/config`:

```conf
output * bg ~/.config/sway/wallpaper/your-wallpaper.jpg fill
```

3. Reload sway: `swaymsg reload`

### Per-theme wallpapers

Each theme can have its own wallpaper. Edit the theme's `theme.conf`:

```bash
# ~/.config/sway/themes/<theme>/theme.conf
wallpaper = your-wallpaper.jpg
```

The theme switcher (`Super+Shift+T`) will patch `output * bg` automatically.

### Fallback

If no wallpaper file exists, sway shows a solid black background. No errors.

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

$mod = Super key. French keyboard layout (`xkb_layout "fr"`).

**Core**
- `$mod+Return` — Open terminal (kitty)
- `$mod+Space` — App launcher (rofi)
- `$mod+q` — Close focused window
- `$mod+Shift+q` — Exit Sway session
- `$mod+Shift+r` — Reload Sway configuration
- `$mod+slash` — Keybind cheatsheet (rofi)

**System**
- `$mod+v` — Toggle clipboard history (cliphist via rofi)
- `$mod+Shift+v` — Clear all clipboard history
- `$mod+n` — Toggle Wi-Fi connection panel
- `$mod+Shift+g` — Launch browser (Brave)
- `$mod+Shift+t` — Theme switcher (12 themes)
- `$mod+Shift+n` — Toggle notification center (swaync)
- `$mod+x` — Power menu (shutdown/reboot)
- `$mod+f` — File manager (Thunar)
- `$mod+e` — Text editor
- `$mod+s` / `$mod+Shift+s` — Full / region screenshot

**Windows & workspaces**
- `$mod+Shift+space` — Toggle floating
- `$mod+Shift+f` — Toggle fullscreen
- `$mod+t` — Cycle layout (split/tabbed/stacking)
- `$mod+w` — Tabbed layout
- `Alt+Tab` / `Alt+Shift+Tab` — Cycle siblings / tabs
- `$mod+h` / `$mod+g` — Horizontal / vertical split
- `$mod+Arrow` — Focus window
- `$mod+Shift+Arrow` — Move window
- `$mod+Ctrl+Arrow` — Resize window
- `$mod+Ctrl+=` — Balance windows
- `$mod+1`–`$mod+0`, `$mod+minus`, `$mod+equal` — Switch to workspace 1–12
- `$mod+Shift+1`–`$mod+Shift+0`, `$mod+Shift+minus`, `$mod+Shift+equal` — Move window to workspace 1–12
- `$mod+Escape` — Cycle to next workspace
- `$mod+Ctrl+n` — Create and jump to next available workspace

**Hardware**
- `Brightness ↑/↓` — Increase / decrease brightness
- `Volume ↑/↓` — Increase / decrease volume
- `$mod+F12`/`$mod+F11`/`$mod+F10` — Volume up/down/mute (custom)

## Credits

Theme system and switcher script adapted from
[justaguylinux/sway-setup](https://codeberg.org/justaguylinux/sway-setup)
(GPL-2.0). Themes by vinceliuice — Orchis (GTK) and Colloid (icons).
