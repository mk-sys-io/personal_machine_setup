# Phase 4 — Sway-Setup Merge

## Problem

The current sway config is a single `sway_config` file with:
- CopyQ clipboard manager (heavy, X11-rooted)
- Fuzzel app launcher (no theme switcher, no power menu)
- Hardcoded Catppuccin Mocha theme (no switching)
- No notification daemon, no screen locker, no idle management
- No screenshot tools, no keybind cheatsheet

[justaguylinux/sway-setup](https://codeberg.org/justaguylinux/sway-setup) ships a polished modular sway config with 12 switchable themes, rofi, kitty, swaync, gtklock, swayidle, grim/slurp, cliphist, and a theme switcher script. It is GPL-2.0 licensed.

Phase 4 extracts the theme system and sway enhancements from sway-setup, merges them with the current system's strengths (French keyboard, custom waybar scripts, Brave browser, lockdown integration), and replaces CopyQ with cliphist.

---

## Decisions

| Decision | Approach | Rationale |
|----------|----------|-----------|
| **Clipboard** | Replace CopyQ with cliphist | cliphist is Wayland-native, minimal (~5MB Go binary), simpler lockdown integration (`cliphist wipe` vs 50-line CopyQ purge) |
| **App launcher** | Replace fuzzel with rofi | rofi enables sway-setup's theme switcher, power menu, keybind cheatsheet — fuzzel can't do these |
| **Terminal** | Add kitty (primary), keep foot (fallback) | kitty is sway-setup's default, better GPU rendering, theming via `kitten themes` |
| **Browser** | Keep Brave, no Firefox | User preference — Brave only |
| **Keyboard** | Keep French `xkb_layout "fr"` | User's working layout |
| **Workspaces** | Expand to 12 | Matches sway-setup, more room, standard |
| **Themes** | Vendor all 12, not submodule | GPL-2.0 requires attribution, not dependency. Vendoring = self-contained, reproducible |
| **Attribution** | Credit in README + LICENSES file | Standard practice for vendored GPL code |
| **xwayland** | Enable (was disabled) | Required by rofi, some apps need it |

---

## Source Analysis

### What we extract from sway-setup (commit `bb7535f3c4`)

**Theme system** (29 files):
- `config/themes/<name>/theme.conf` × 12 themes (7 color vars + app names each)
- `config/themes/<name>/colors.conf` × 12 themes (6 sway variables each)
- `config/themes/_templates/` — waybar.css, rofi/*.rasi, swaync/style.css (template files with `{{bg}}` placeholders)
- `config/scripts/thememenu` — theme switcher script (~120 lines bash)

**Sway config structure:**
- `config/config` — main config (variables, output, input, appearance, includes)
- `config/keybindings.conf` — all bindsym lines
- `config/workspaces.conf` — workspace vars + bindings (12 workspaces)
- `config/rules.conf` — for_window rules, borders, gaps

**Scripts:**
- `config/scripts/autostart.sh` — swaync, waybar, cliphist, portals
- `config/scripts/screenshot` — grim/slurp wrapper
- `config/scripts/changevolume` — volume + notifications
- `config/scripts/power` — rofi power menu
- `config/scripts/help` — rofi keybind cheatsheet

**Components:**
- `config/waybar/config-glyphs` — waybar config with glyph-based modules
- `config/rofi/config.rasi` — rofi launcher theme
- `config/rofi/keybinds.rasi` — rofi keybind display theme
- `config/rofi/power.rasi` — rofi power menu theme
- `config/swaync/config.json` — notification daemon config
- `config/swaync/style.css` — notification styling (template-rendered)
- `config/gtklock/config.ini` — screen locker config
- `config/gtklock/style.css` — screen locker styling

### What we DON'T extract

| Item | Reason |
|------|--------|
| sway-setup's `install.sh` | We have our own modular orchestrator |
| Package lists | We add to our own `packages/apt.txt` |
| NVIDIA setup | Handled by our `30-hardware.sh` |
| Display manager installer | Out of scope |
| Optional tools installer | Out of scope |
| Butterrepo setup | Not needed — we install via apt |

---

## Lockdown Impact

### Current CopyQ dependencies (3 files)

| File | Current behavior | New behavior |
|------|-----------------|--------------|
| `lockdown/lib/clipboard-clear.sh` | Tries `copyq clear`, falls back to `wl-copy --clear` | Tries `cliphist wipe`, falls back to `wl-copy --clear` |
| `lockdown/allowlist/scripts/seal_lib.py` | `discover_session()` probes CopyQ process for D-Bus env; `clear_clipboard(purge=True)` kills CopyQ, deletes data files | `discover_session()` probes `sway`/`waybar` only; `clear_clipboard(purge=True)` runs `cliphist wipe` + `wl-copy --clear` |
| `lockdown/sudoers/99-mike-tools` | Grants sudo for `copyq` | Grants sudo for `cliphist` |

### `60-lockdown.sh` itself: NO CHANGES

The lockdown orchestrator deploys files — it doesn't contain clipboard logic. We change the **deployed files**, not the deployer.

---

## config.env changes

Add to `config.env.template`:

```bash
# ── Sway desktop (Phase 4) ──
PRIMARY_TERMINAL=kitty
APP_LAUNCHER=rofi
```

These variables are used in the sway config:
- `PRIMARY_TERMINAL` → `set $term kitty` (can be overridden)
- `APP_LAUNCHER` → `set $menu rofi -show drun` (can be overridden)

The existing `TERMINAL=/usr/bin/foot` stays as the system fallback (used by `lockdown/lib/terminal` adapter).

---

## Execution

### Step 1 — Extract theme system

Create directory structure:

```
dotfiles/sway/
├── themes/
│   ├── _templates/
│   │   ├── waybar.css
│   │   ├── rofi/
│   │   │   ├── config.rasi
│   │   │   ├── keybinds.rasi
│   │   │   └── power.rasi
│   │   └── swaync/
│   │       └── style.css
│   ├── catppuccin/
│   │   ├── theme.conf
│   │   └── colors.conf
│   ├── doom_one/
│   │   ├── theme.conf
│   │   └── colors.conf
│   ├── dracula/
│   ├── everforest/
│   ├── github_dark/        (default)
│   ├── gruvbox/
│   ├── kanagawa/
│   ├── monokai/
│   ├── moonfly/
│   ├── nord/
│   ├── retro/
│   └── rose_pine_moon/
└── wallpaper/              (populated separately)
```

Each `theme.conf` contains:
```conf
name = Theme Name
kitty   = Kitty-Theme-Name
gtk     = Orchis-Variant
icons   = Colloid-Variant
wallpaper = wallhaven-xxx.png
bg        = #hex
bg_alt    = #hex
fg        = #hex
primary   = #hex
secondary = #hex
disabled  = #hex
alert     = #hex
```

Each `colors.conf` contains:
```conf
set $bg #hex
set $fg #hex
set $gray #hex
set $cyan #hex
set $barbie #hex
set $blue #hex
```

### Step 2 — Extract sway config (modular)

Replace single `dotfiles/sway/sway_config` with:

```
dotfiles/sway/
├── config                    (main — variables, output, input, appearance, includes)
├── keybindings.conf          (merged: current + sway-setup)
├── workspaces.conf           (12 workspaces)
├── rules.conf                (for_window rules, borders, gaps)
├── scripts/
│   ├── autostart.sh          (swaync, waybar, cliphist, portals)
│   ├── screenshot            (grim/slurp wrapper)
│   ├── changevolume          (volume + notifications)
│   ├── thememenu             (theme switcher)
│   ├── power                 (rofi power menu)
│   └── help                  (rofi keybind cheatsheet)
├── waybar/
│   ├── config-glyphs
│   └── style-glyphs.css
├── rofi/
│   ├── config.rasi
│   ├── keybinds.rasi
│   └── power.rasi
├── swaync/
│   ├── config.json
│   └── style.css
├── gtklock/
│   ├── config.ini
│   └── style.css
├── foot/
│   └── foot.ini
├── themes/
│   └── ... (from Step 1)
└── wallpaper/
```

### Step 3 — Merge keybindings

Combine current system's unique bindings with sway-setup's:

| Binding | Source | Action |
|---------|--------|--------|
| `$mod+Return` | Both | Launch terminal (`$term` = kitty) |
| `$mod+Space` | sway-setup | App launcher (rofi) |
| `$mod+q` | sway-setup | Close window |
| `$mod+Shift+q` | Both | Exit sway |
| `$mod+Shift+c` | Current | Reload sway |
| `$mod+v` | Current | Toggle clipboard history (rofi + cliphist) |
| `$mod+Shift+v` | Current | Clear clipboard |
| `$mod+n` | Current | Toggle WiFi panel |
| `$mod+Shift+g` | Current | Launch Brave |
| `$mod+b` | sway-setup | Launch Firefox (disabled, Brave only) |
| `$mod+f` | sway-setup | File manager (Thunar) |
| `$mod+e` | sway-setup | Text editor |
| `$mod+Shift+t` | sway-setup | Theme switcher |
| `$mod+Shift+n` | sway-setup | Notification center |
| `$mod+x` | sway-setup | Power menu |
| `$mod+slash` | sway-setup | Keybind cheatsheet |
| `$mod+s` | sway-setup | Full screenshot |
| `$mod+Shift+s` | sway-setup | Region screenshot |
| `$mod+Escape` | Current | Cycle workspaces |
| `$mod+Ctrl+n` | Current | Create next workspace |
| `$mod+1-9,0,-,=` | sway-setup | Switch workspace 1-12 |
| `$mod+Shift+1-9,0,-,=` | sway-setup | Move to workspace 1-12 |
| `$mod+Left/Down/Up/Right` | Both | Focus window |
| `$mod+Shift+Left/Down/Up/Right` | Both | Move window |
| `$mod+Ctrl+Left/Down/Up/Right` | sway-setup | Resize window |
| `$mod+Ctrl+=` | sway-setup | Balance windows |
| `$mod+f` | Current | Fullscreen toggle |
| `$mod+h` / `$mod+g` | Current | Horizontal/vertical split |
| `$mod+t` | sway-setup | Cycle layout |
| `$mod+w` | sway-setup | Tabbed layout |
| `Alt+Tab` | sway-setup | Cycle siblings |
| `XF86*` | Both | Hardware keys (brightness, volume) |

### Step 4 — Create kitty config

Create `dotfiles/kitty/kitty.conf`:

```conf
# Font
font_family      JetBrains Mono
font_size        11.0

# Cursor
cursor_shape     block
cursor_blink_interval 0

# Scrollback
scrollback_lines 10000

# Mouse
copy_on_select   clipboard
mouse_hide_wait  3.0

# Bell
enable_audio_bell no
visual_bell_duration 0.0

# Window
window_padding_width 12
hide_window_decorations yes
confirm_os_window_close 0

# Tab bar
tab_bar_edge   bottom
tab_bar_style  powerline
tab_powerline_style slanted

# Shell
shell integration enabled

# Include theme (symlinked by thememenu)
include current-theme.conf
```

Create `dotfiles/kitty/current-theme.conf` (default Catppuccin Mocha):

```conf
# Catppuccin Mocha
foreground           #CDD6F4
background           #1E1E2E
selection_foreground  #1E1E2E
selection_background  #F5E0DC
cursor               #F5E0DC
cursor_text_color    #1E1E2E
url_color            #F5E0DC
active_tab_foreground   #11111B
active_tab_background   #CBA6F7
inactive_tab_foreground #CDD6F4
inactive_tab_background #181825

# Normal colors
color0 #45475A
color1 #F38BA8
color2 #A6E3A1
color3 #F9E2AF
color4 #89B4FA
color5 #CBA6F7
color6 #94E2D5
color7 #BAC2DE

# Bright colors
color8  #585B70
color9  #F38BA8
color10 #A6E3A1
color11 #F9E2AF
color12 #89B4FA
color13 #CBA6F7
color14 #94E2D5
color15 #A6ADC8
```

### Step 5 — Update Makefile

Replace the `dotfiles` target:

```makefile
dotfiles:
	@echo "=== Dotfiles ==="
	# bashrc
	cp dotfiles/bashrc $(HOME)/.bashrc
	$(SUBST) $(HOME)/.bashrc
	# app config dirs
	for app in foot kitty sway waybar rofi swaync gtklock ranger fzf; do \
		mkdir -p $(DEPLOY_DIR)/$$app; \
		cp -r dotfiles/$$app/* $(DEPLOY_DIR)/$$app/; \
	done
	# symlinks for apps that expect default locations
	ln -sf $(DEPLOY_DIR)/sway/gtklock $(DEPLOY_DIR)/gtklock
	ln -sf $(DEPLOY_DIR)/sway/foot $(DEPLOY_DIR)/foot
	# brave/firefox (policy dirs)
	mkdir -p $(DEPLOY_DIR)/brave
	cp -r dotfiles/brave/*   $(DEPLOY_DIR)/brave/
	mkdir -p $(DEPLOY_DIR)/firefox
	cp -r dotfiles/firefox/* $(DEPLOY_DIR)/firefox/
	# waybar scripts subdir
	mkdir -p $(DEPLOY_DIR)/waybar/scripts
	cp -r dotfiles/waybar/scripts/* $(DEPLOY_DIR)/waybar/scripts/
	# obsidian (custom vault path)
	mkdir -p $(OBSIDIAN_VAULT_PATH)/.obsidian
	cp dotfiles/obsidian/* $(OBSIDIAN_VAULT_PATH)/.obsidian/
	# set default theme symlink
	ln -sfn themes/github_dark $(DEPLOY_DIR)/sway/current-theme
	@echo "Dotfiles deployed."
```

**Changes:**
- Add `kitty`, `rofi`, `swaync`, `gtklock` to app loop
- Remove `copyq`, `fuzzel`
- Add `current-theme` symlink to github_dark (default)
- Add symlinks for gtklock and foot (apps that expect `~/.config/<name>`)

### Step 6 — Update packages/apt.txt

Append these lines:

```
# Sway enhancements (Phase 4)
swayidle
gtklock
swaybg
xwayland
build-essential
wmenu
sway-notification-center
autotiling
grim
slurp
cliphist
playerctl
wlr-randr
xdg-desktop-portal-wlr
swappy
wtype
rofi
nwg-look
xsettingsd
network-manager-gnome
lxpolkit
thunar
thunar-archive-plugin
thunar-volman
gvfs-backends
dialog
mtools
smbclient
cifs-utils
pavucontrol
pulsemixer
pamixer
pipewire-audio
cmake
meson
ninja-build
pkg-config
wget
fonts-recommended
fonts-font-awesome
fonts-noto-color-emoji
avahi-daemon
acpi
acpid
xdg-user-dirs-gtk
kanshi
eog
nwg-displays
gawk
libnotify-bin
libnotify-dev
libusb-0.1-4
```

### Step 7 — Add cliphist to packages/go_installs.txt

Append:

```
cliphist|github.com/sentriz/cliphist|v0.7.0
```

### Step 8 — Update lockdown files

#### 8a. `lockdown/lib/clipboard-clear.sh`

```bash
#!/bin/bash
# Adapter: clear clipboard — swap cliphist/copyq/wl-clipboard here, not in seal
for tool in cliphist wl-copy; do
    if command -v "$tool" &>/dev/null; then
        case "$tool" in
            cliphist) cliphist wipe ;;
            wl-copy)  wl-copy --clear ;;
        esac
        exit 0
    fi
done
echo "clipboard-clear: no supported clipboard tool found" >&2
exit 1
```

#### 8b. `lockdown/allowlist/scripts/seal_lib.py`

Two changes:

**`discover_session()`** — remove `copyq` from process list:
```python
# BEFORE:
for proc in ["sway", "waybar", "copyq"]:
# AFTER:
for proc in ["sway", "waybar"]:
```

**`clear_clipboard(purge=True)`** — replace CopyQ purge with cliphist:
```python
# BEFORE (lines 284-371): CopyQ D-Bus clear, kill process, delete data files
# AFTER:
subprocess.run(["cliphist", "wipe"], capture_output=True, timeout=10)
subprocess.run(["wl-copy", "--clear"], capture_output=True, timeout=10)
```

This removes ~80 lines of CopyQ-specific logic (D-Bus discovery, process killing, file deletion).

#### 8c. `lockdown/sudoers/99-mike-tools`

```diff
- @USERNAME@ ALL=(@USERNAME@) NOPASSWD: /usr/bin/copyq
+ @USERNAME@ ALL=(@USERNAME@) NOPASSWD: /usr/bin/cliphist
```

### Step 9 — Update waybar scripts

#### 9a. `dotfiles/waybar/scripts/clear-clipboard.sh`

Replace CopyQ eval with cliphist:

```bash
#!/bin/bash
cliphist wipe && wl-copy --clear
```

#### 9b. `dotfiles/waybar/scripts/toggle-nmtui.sh`

Update to use `$TERMINAL` or fall back to foot:

```bash
#!/bin/bash
TERMINAL="${TERMINAL:-foot}"

if pgrep -f "$TERMINAL.*nmtui" > /dev/null 2>&1; then
    pkill -f "$TERMINAL.*nmtui"
else
    $TERMINAL nmtui
fi
```

### Step 10 — Remove old files

| File | Action |
|------|--------|
| `dotfiles/sway/sway_config` | Delete (replaced by modular config) |
| `dotfiles/copyq/` | Delete (replaced by cliphist) |
| `dotfiles/fuzzel/` | Delete (replaced by rofi) |
| `dotfiles/waybar/style.css` | Delete (replaced by themes/_templates/waybar.css) |
| `dotfiles/waybar/mocha.css` | Delete (replaced by theme system) |

### Step 11 — Attribution

Add to `README.md`:

```markdown
## Credits

Theme system and switcher script adapted from
[justaguylinux/sway-setup](https://codeberg.org/justaguylinux/sway-setup)
(GPL-2.0). Themes by vinceliuice — Orchis (GTK) and Colloid (icons).
```

Create `LICENSES/GPL-2.0.txt` with the full GPL-2.0 license text (vendored files carry this license).

---

## Deployment Order

```
install.sh
  ├── 20-packages.sh reads packages/apt.txt → installs sway, rofi, grim, slurp, cliphist, kitty, etc.
  ├── 20-packages.sh reads packages/go_installs.txt → builds cliphist from source
  ├── make all → deploys sway config, kitty config, rofi config, waybar, scripts, themes
  └── 60-lockdown.sh → deploys updated clipboard-clear.sh, seal_lib.py, sudoers
```

No code changes to `install.sh`, `20-packages.sh`, or `60-lockdown.sh`. Only:
- Text file additions (`packages/apt.txt`, `packages/go_installs.txt`)
- New directories (`dotfiles/sway/themes/`, `dotfiles/kitty/`, `dotfiles/rofi/`, etc.)
- Makefile target update (app list in `dotfiles` target)
- Lockdown file updates (3 files)

---

## Files changed / created

**Create**
- `dotfiles/sway/config` (main sway config)
- `dotfiles/sway/keybindings.conf`
- `dotfiles/sway/workspaces.conf`
- `dotfiles/sway/rules.conf`
- `dotfiles/sway/scripts/autostart.sh`
- `dotfiles/sway/scripts/screenshot`
- `dotfiles/sway/scripts/changevolume`
- `dotfiles/sway/scripts/thememenu`
- `dotfiles/sway/scripts/power`
- `dotfiles/sway/scripts/help`
- `dotfiles/sway/waybar/config-glyphs`
- `dotfiles/sway/waybar/style-glyphs.css`
- `dotfiles/sway/rofi/config.rasi`
- `dotfiles/sway/rofi/keybinds.rasi`
- `dotfiles/sway/rofi/power.rasi`
- `dotfiles/sway/swaync/config.json`
- `dotfiles/sway/swaync/style.css`
- `dotfiles/sway/gtklock/config.ini`
- `dotfiles/sway/gtklock/style.css`
- `dotfiles/sway/themes/_templates/waybar.css`
- `dotfiles/sway/themes/_templates/rofi/config.rasi`
- `dotfiles/sway/themes/_templates/rofi/keybinds.rasi`
- `dotfiles/sway/themes/_templates/rofi/power.rasi`
- `dotfiles/sway/themes/_templates/swaync/style.css`
- `dotfiles/sway/themes/catppuccin/theme.conf`
- `dotfiles/sway/themes/catppuccin/colors.conf`
- `dotfiles/sway/themes/doom_one/theme.conf`
- `dotfiles/sway/themes/doom_one/colors.conf`
- `dotfiles/sway/themes/dracula/theme.conf`
- `dotfiles/sway/themes/dracula/colors.conf`
- `dotfiles/sway/themes/everforest/theme.conf`
- `dotfiles/sway/themes/everforest/colors.conf`
- `dotfiles/sway/themes/github_dark/theme.conf`
- `dotfiles/sway/themes/github_dark/colors.conf`
- `dotfiles/sway/themes/gruvbox/theme.conf`
- `dotfiles/sway/themes/gruvbox/colors.conf`
- `dotfiles/sway/themes/kanagawa/theme.conf`
- `dotfiles/sway/themes/kanagawa/colors.conf`
- `dotfiles/sway/themes/monokai/theme.conf`
- `dotfiles/sway/themes/monokai/colors.conf`
- `dotfiles/sway/themes/moonfly/theme.conf`
- `dotfiles/sway/themes/moonfly/colors.conf`
- `dotfiles/sway/themes/nord/theme.conf`
- `dotfiles/sway/themes/nord/colors.conf`
- `dotfiles/sway/themes/retro/theme.conf`
- `dotfiles/sway/themes/retro/colors.conf`
- `dotfiles/sway/themes/rose_pine_moon/theme.conf`
- `dotfiles/sway/themes/rose_pine_moon/colors.conf`
- `dotfiles/kitty/kitty.conf`
- `dotfiles/kitty/current-theme.conf`
- `LICENSES/GPL-2.0.txt`

**Modify**
- `packages/apt.txt` (add ~50 packages)
- `packages/go_installs.txt` (add cliphist)
- `Makefile` (update `dotfiles` target app list)
- `config.env.template` (add PRIMARY_TERMINAL, APP_LAUNCHER)
- `config.env` (add PRIMARY_TERMINAL, APP_LAUNCHER)
- `lockdown/lib/clipboard-clear.sh` (add cliphist)
- `lockdown/allowlist/scripts/seal_lib.py` (remove CopyQ, use cliphist)
- `lockdown/sudoers/99-mike-tools` (replace copyq with cliphist)
- `dotfiles/waybar/scripts/clear-clipboard.sh` (replace CopyQ eval)
- `dotfiles/waybar/scripts/toggle-nmtui.sh` (use $TERMINAL)
- `README.md` (add credits)

**Delete**
- `dotfiles/sway/sway_config` (replaced by modular config)
- `dotfiles/copyq/` (replaced by cliphist)
- `dotfiles/fuzzel/` (replaced by rofi)
- `dotfiles/waybar/style.css` (replaced by theme system)
- `dotfiles/waybar/mocha.css` (replaced by theme system)

**NOT modified**
- `install.sh` (orchestrator unchanged)
- `lib/20-packages.sh` (module unchanged — reads text files)
- `lib/60-lockdown.sh` (orchestrator unchanged — deploys files)
- `dotfiles/foot/foot.ini` (stays as fallback terminal)

---

## Testing

1. Run `bash lib/20-packages.sh` — verify new packages install (rofi, kitty, grim, slurp, cliphist, swaync, gtklock, swayidle, etc.)
2. Verify cliphist installed: `which cliphist` and `cliphist version`
3. Verify kitty installed: `which kitty`
4. Run `make all` — verify new sway config lands in `~/.config/sway/`
5. Verify theme structure: `ls ~/.config/sway/themes/` shows 12 themes
6. Verify kitty config: `ls ~/.config/kitty/` shows kitty.conf and current-theme.conf
7. Verify rofi config: `ls ~/.config/sway/rofi/` shows config.rasi, keybinds.rasi, power.rasi
8. Log out and log back into sway
9. Test `Super+Space` — rofi launcher should appear
10. Test `Super+Shift+T` — theme switcher should show 12 themes
11. Test `Super+Shift+S` — screenshot should work (grim+slurp)
12. Test `Super+V` — cliphist history should appear via rofi
13. Test `Super+X` — power menu should appear
14. Test `Super+/` — keybind cheatsheet should appear
15. Test `Super+Return` — kitty terminal should launch
16. Verify waybar displays correctly with theme colors
17. Verify swaync notifications work
18. Run lockdown: `sudo bash lib/60-lockdown.sh`
19. Test seal flow: verify clipboard-clear adapter uses cliphist
20. Verify no hardcoded `copyq` in deployed lockdown files: `grep -r copyq /opt/allowlist/ /usr/local/lib/lockdown/`

---

## Revert plan

If the merge causes issues:

1. Restore `dotfiles/sway/sway_config` from git history
2. Restore `dotfiles/copyq/` from git history
3. Restore `dotfiles/fuzzel/` from git history
4. Restore `dotfiles/waybar/style.css` and `mocha.css` from git history
5. Remove new files: `dotfiles/sway/themes/`, `dotfiles/kitty/`, `dotfiles/sway/rofi/`, etc.
6. Revert `packages/apt.txt` changes
7. Revert `lockdown/lib/clipboard-clear.sh`, `seal_lib.py`, `sudoers`
8. Revert `Makefile` dotfiles target
9. Run `make all && sudo bash lib/60-lockdown.sh`

All changes are in tracked files — `git checkout` restores everything.
