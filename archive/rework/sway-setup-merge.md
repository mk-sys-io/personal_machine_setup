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
| **File removal** | Archive only, no deletion | Move replaced files to `archive/` for safekeeping; clean up later |
| **Stale packages** | List separately, remove after stabilization | Avoid breaking working installs; remove after system is confirmed stable |

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

### Current CopyQ dependencies (7 files)

| File | Current behavior | New behavior |
|------|-----------------|--------------|
| `lockdown/lib/clipboard-clear.sh` | Tries `copyq clear`, falls back to `wl-copy --clear` | Tries `cliphist wipe`, falls back to `wl-copy --clear` |
| `lockdown/allowlist/scripts/seal_lib.py` | `discover_session()` probes CopyQ process for D-Bus env; `clear_clipboard(purge=True)` kills CopyQ, deletes data files | `discover_session()` probes `sway`/`waybar` only; `clear_clipboard(purge=True)` runs `cliphist wipe` + `wl-copy --clear` as user |
| `lockdown/allowlist/scripts/allowlist.sh` | Probes CopyQ for D-Bus env; CopyQ D-Bus clear; kills CopyQ; deletes CopyQ data files (~80 lines) | Probes `sway`/`waybar` only; runs `cliphist wipe` + `wl-copy --clear`; removes ~80 lines of CopyQ logic |
| `lockdown/allowlist/scripts/seal.py` | UI string: `"Clear clipboard history (copyq + wl-copy)"` | Update to `"Clear clipboard history (cliphist + wl-copy)"` |
| `lockdown/allowlist/scripts/sem.py` | UI string: `"Clear clipboard history (copyq + wl-copy)"` | Update to `"Clear clipboard history (cliphist + wl-copy)"` |
| `lockdown/sudoers/99-mike-tools` | Grants sudo for `copyq` | Grants sudo for `cliphist` |
| `Makefile` (lines 23-25) | Deploys `dotfiles/copyq/themes/*` to `~/.config/copyq/themes/` | Remove (copyq archived) |

### `60-lockdown.sh` itself: NO CHANGES

The lockdown orchestrator deploys files — it doesn't contain clipboard logic. We change the **deployed files**, not the deployer.

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
└── wallpaper/              (populated separately — see Step 11)
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

**Note:** The old `sway_config` contains a CopyQ window rule (`for_window [app_id="com.github.hluk.copyq"]`). This is dropped — the new config is written from scratch without it.

**Existing scripts that survive unchanged:** `network.sh`, `volume.sh`, `brightness.sh`, `numlock.sh`, `launch-browser.sh` — these are deployed via the Makefile app loop to `~/.config/waybar/scripts/`. Audit `config-glyphs` during implementation to confirm which ones it references; remove any that are dead.

```
dotfiles/sway/
├── config                    (main — variables, output, input, appearance, includes)
├── keybindings.conf          (merged: current + sway-setup)
├── workspaces.conf           (12 workspaces)
├── rules.conf                (for_window rules, borders, gaps)
├── scripts/
│   ├── autostart.sh          (swaync, waybar, cliphist, portals — replaces launch-waybar.sh)
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
│   └── foot.ini            (move from existing `dotfiles/foot/foot.ini`)
├── themes/
│   └── ... (from Step 1)
└── wallpaper/
```

### Step 3 — Merge keybindings

Combine current system's unique bindings with sway-setup's. Key changes from current config:

| Binding | Source | Action |
|---------|--------|--------|
| `$mod+Return` | Both | Launch terminal (`$term` = kitty) |
| `$mod+Space` | sway-setup | App launcher (rofi) — **replaces `$mod+d` fuzzel toggle** |
| `$mod+q` | sway-setup | Close window — **was `$mod+Shift+q` kill** |
| `$mod+Shift+q` | sway-setup | Exit sway — **was `$mod+Shift+e`** |
| `$mod+Shift+r` | sway-setup | Reload sway — **was `$mod+Shift+c`** |
| `$mod+Shift+f` | sway-setup | Fullscreen toggle — **was `$mod+f`** |
| `$mod+Shift+space` | sway-setup | Toggle floating |
| `$mod+v` | Current | Toggle clipboard history (rofi + cliphist) — **replaces CopyQ** |
| `$mod+Shift+v` | Current | Clear clipboard |
| `$mod+n` | Current | Toggle WiFi panel |
| `$mod+Shift+g` | Current | Launch Brave — **replaces `$mod+b` Firefox** (keybindings.conf must reference `~/.config/waybar/scripts/launch-browser.sh`) |
| `$mod+b` | sway-setup | Launch Firefox — **disabled (Brave only)** |
| `$mod+f` | sway-setup | File manager (Thunar) — **was fullscreen** |
| `$mod+e` | sway-setup | Text editor |
| `$mod+Shift+t` | sway-setup | Theme switcher |
| `$mod+Shift+n` | sway-setup | Notification center |
| `$mod+x` | sway-setup | Power menu |
| `$mod+slash` | sway-setup | Keybind cheatsheet |
| `$mod+s` | sway-setup | Full screenshot |
| `$mod+Shift+s` | sway-setup | Region screenshot |
| `$mod+Escape` | Current | Cycle workspaces |
| `$mod+Ctrl+n` | Current | Create next workspace |
| `$mod+1-9,0,-,=` | sway-setup | Switch workspace 1-12 — **was 1-5** |
| `$mod+Shift+1-9,0,-,=` | sway-setup | Move to workspace 1-12 — **was 1-5** |
| `$mod+Left/Down/Up/Right` | Both | Focus window |
| `$mod+Shift+Left/Down/Up/Right` | Both | Move window |
| `$mod+Ctrl+Left/Down/Up/Right` | sway-setup | Resize window |
| `$mod+Ctrl+=` | sway-setup | Balance windows |
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
shell_integration enabled

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

**⚠️ Execute Step 9 (Archive old files) BEFORE running `make all`** — otherwise the app loop will deploy archived files (copyq/, fuzzel/, style.css, etc.).

Replace the `dotfiles` target. Key changes from current Makefile:
- Add `kitty`, `rofi`, `swaync` to app loop
- Remove `copyq`, `fuzzel` from app loop
- Remove copyq theme subdir deployment (lines 23-25: `mkdir -p copyq/themes`, `cp -r copyq/themes/*`)
- Exclude `foot` and `gtklock` from app loop (deployed via symlinks instead)
- Create symlinks for `foot` and `gtklock` → `sway/foot` and `sway/gtklock`
- Add `current-theme` symlink to github_dark (default)
- Move symlink creation **before** app loop to avoid directory conflicts
- Add waybar scripts deployment

```makefile
dotfiles:
	@echo "=== Dotfiles ==="
	# bashrc
	cp dotfiles/bashrc $(HOME)/.bashrc
	$(SUBST) $(HOME)/.bashrc
	# symlinks for apps that expect default locations (create BEFORE app loop)
	mkdir -p $(DEPLOY_DIR)/sway/foot $(DEPLOY_DIR)/sway/gtklock
	ln -sfn $(DEPLOY_DIR)/sway/foot $(DEPLOY_DIR)/foot
	ln -sfn $(DEPLOY_DIR)/sway/gtklock $(DEPLOY_DIR)/gtklock
	# set default theme symlink
	mkdir -p $(DEPLOY_DIR)/sway/themes
	ln -sfn themes/github_dark $(DEPLOY_DIR)/sway/current-theme
	# app config dirs (foot/gtklock excluded — deployed via symlinks above)
	for app in kitty sway waybar rofi swaync ranger fzf; do \
		mkdir -p $(DEPLOY_DIR)/$$app; \
		cp -r dotfiles/$$app/* $(DEPLOY_DIR)/$$app/; \
	done
	# brave/firefox (policy dirs)
	mkdir -p $(DEPLOY_DIR)/brave
	cp -r dotfiles/brave/*   $(DEPLOY_DIR)/brave/
	mkdir -p $(DEPLOY_DIR)/firefox
	cp -r dotfiles/firefox/* $(DEPLOY_DIR)/firefox/
	# obsidian (custom vault path)
	mkdir -p $(OBSIDIAN_VAULT_PATH)/.obsidian
	cp dotfiles/obsidian/* $(OBSIDIAN_VAULT_PATH)/.obsidian/
	@echo "Dotfiles deployed."
```

### Step 6 — Update packages/apt.txt

Append new packages:

```
# Sway enhancements (Phase 4)
swayidle
gtklock
swaybg
xwayland
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

**Do NOT remove** `copyq`, `copyq-plugins`, or `fuzzel` from apt.txt yet. See Step 12 for stale package tracking.

### Step 7 — Update lockdown files

#### 7a. `lockdown/lib/clipboard-clear.sh`

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

#### 7b. `lockdown/allowlist/scripts/seal_lib.py`

Three changes:

**`discover_session()`** — remove `copyq` from process list:
```python
# BEFORE:
for proc in ["sway", "waybar", "copyq"]:
# AFTER:
for proc in ["sway", "waybar"]:
```

**`clear_clipboard(purge=True)`** — replace CopyQ purge with cliphist (run as user, not root, with Wayland env vars):
```python
# BEFORE (lines 284-371): CopyQ D-Bus clear, kill process, delete data files
# AFTER (keep discover_session() call before this block to populate wayland_display/xdg_data_home):
env = {
    "XDG_RUNTIME_DIR": f"/run/user/{MIKE_UID}",
    "WAYLAND_DISPLAY": wayland_display,
    "XDG_DATA_HOME": xdg_data_home,
}
try:
    subprocess.run(["sudo", "-u", f"#{MIKE_UID}", "cliphist", "wipe"],
                   env=env, capture_output=True, timeout=10)
    subprocess.run(["sudo", "-u", f"#{MIKE_UID}", "wl-copy", "--clear"],
                   env=env, capture_output=True, timeout=10)
except FileNotFoundError:
    # cliphist not installed — fall back to wl-copy only
    subprocess.run(["sudo", "-u", f"#{MIKE_UID}", "wl-copy", "--clear"],
                   env=env, capture_output=True, timeout=10)
```

This removes ~80 lines of CopyQ-specific logic (D-Bus discovery, process killing, file deletion). `WAYLAND_DISPLAY` and `XDG_RUNTIME_DIR` are required for both `cliphist` and `wl-copy` to communicate with the compositor. `try/except` handles missing cliphist binary.

**Comments** — update CopyQ references:
```python
# BEFORE (line 267):
# Simple clear: delegate to adapter (handles copyq/wl-copy detection)
# AFTER:
# Simple clear: delegate to adapter (handles cliphist/wl-copy detection)
```
Remove or replace any purge-mode comment referencing "D-Bus env for copyq + file cleanup".

#### 7c. `lockdown/allowlist/scripts/allowlist.sh`

Remove CopyQ-specific logic (lines 258, 360-438). Replace with cliphist:

**Confirmation prompt** — update UI string:
```bash
# BEFORE (line 258):
echo "    - Clear clipboard history (copyq + wl-copy)"
# AFTER:
echo "    - Clear clipboard history (cliphist + wl-copy)"
```

**Session discovery** — remove CopyQ from process probe list:
```bash
# BEFORE (line 365):
MIKE_PID=$(pgrep -u mike -x copyq 2>/dev/null | head -1)
# REMOVE this line — only sway/waybar needed
```

**Clipboard clear** — replace CopyQ D-Bus clear + kill + file deletion with:
```bash
# ── 2. Cliphist wipe (replaces CopyQ D-Bus clear) ──
if sudo -u mike \
    XDG_RUNTIME_DIR="$XDG_RUNTIME_DIR_ENV" \
    WAYLAND_DISPLAY="$MIKE_WAYLAND_DISPLAY" \
    cliphist wipe 2>/dev/null; then
    echo "  [OK] Cliphist history wiped"
else
    echo "  [--] cliphist wipe failed"
fi
```

Remove lines 393-438 entirely (CopyQ D-Bus clear, kill CopyQ, delete CopyQ data files).

#### 7d. `lockdown/allowlist/scripts/seal.py`

Update UI string at line 192:
```python
# BEFORE:
"Clear clipboard history (copyq + wl-copy)",
# AFTER:
"Clear clipboard history (cliphist + wl-copy)",
```

#### 7e. `lockdown/allowlist/scripts/sem.py`

Update UI string at line 50:
```python
# BEFORE:
"Clear clipboard history (copyq + wl-copy)",
# AFTER:
"Clear clipboard history (cliphist + wl-copy)",
```

#### 7f. `lockdown/sudoers/99-mike-tools`

```diff
- @USERNAME@ ALL=(@USERNAME@) NOPASSWD: /usr/bin/copyq
+ @USERNAME@ ALL=(@USERNAME@) NOPASSWD: /usr/bin/cliphist
```

Note: `/usr/bin/cliphist` is the apt-installed path. If cliphist is installed from source instead, use `/usr/local/bin/cliphist`.

### Step 8 — Update waybar scripts

#### 8a. `dotfiles/waybar/scripts/clear-clipboard.sh`

Replace CopyQ eval with cliphist:

```bash
#!/bin/bash
cliphist wipe && wl-copy --clear
```

#### 8b. `dotfiles/waybar/scripts/toggle-nmtui.sh`

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

#### 8c. Update `manual_work.md`

Update documentation references to CopyQ and fuzzel (lines 24, 27, 315):

Line 24 — fix pre-existing bug (make config → make dotfiles):
```markdown
# BEFORE:
make config
# AFTER:
make dotfiles
```

Line 27 — update app list:
```markdown
# BEFORE:
This copies sway, waybar, foot, fuzzel, copyq, opencode, and obsidian
# AFTER:
This copies sway, waybar, foot, rofi, kitty, opencode, and obsidian
```

Line 315 — update clipboard reference:
```markdown
# BEFORE:
and copies the root password to the clipboard automatically (copyq, fallback wl-copy).
# AFTER:
and displays the root password for manual clipboard copy (cliphist history).
```

#### 8d. Archive `dotfiles/waybar/scripts/launch-waybar.sh`

This script hardcodes `~/.config/waybar/config.json` which no longer exists. sway-setup's autostart.sh launches waybar directly with the correct paths:

```bash
pkill -x waybar
waybar -c ~/.config/sway/waybar/config-glyphs -s ~/.config/sway/waybar/style-glyphs.css &
```

Move to `archive/rework/launch-waybar.sh`.

### Step 9 — Archive old files

Move replaced files to `archive/rework/` (not deleted, preserved for rollback):

| File | Action |
|------|--------|
| `dotfiles/sway/sway_config` | → `archive/rework/sway_config` (replaced by modular config) |
| `dotfiles/copyq/` | → `archive/rework/copyq/` (replaced by cliphist) |
| `dotfiles/fuzzel/` | → `archive/rework/fuzzel/` (replaced by rofi) |
| `dotfiles/waybar/style.css` | → `archive/rework/waybar-style.css` (replaced by themes/_templates/waybar.css) |
| `dotfiles/waybar/mocha.css` | → `archive/rework/waybar-mocha.css` (replaced by theme system) |
| `dotfiles/waybar/scripts/launch-waybar.sh` | → `archive/rework/launch-waybar.sh` (replaced by autostart.sh) |
| `dotfiles/waybar/waybar_config.json` | → `archive/rework/waybar_config.json` (autostart.sh uses config-glyphs instead) |

### Step 10 — Attribution

Add to `README.md`:

```markdown
## Credits

Theme system and switcher script adapted from
[justaguylinux/sway-setup](https://codeberg.org/justaguylinux/sway-setup)
(GPL-2.0). Themes by vinceliuice — Orchis (GTK) and Colloid (icons).
```

Create `LICENSES/GPL-2.0.txt` with the full GPL-2.0 license text (vendored files carry this license).

### Step 11 — Wallpaper setup

The `dotfiles/sway/wallpaper/` directory is empty by default. Users must set a wallpaper manually before first login.

Add to `README.md` (near top, after Quick start):

```markdown
## Wallpaper setup

After install, place a wallpaper in the sway config:

```bash
mkdir -p ~/.config/sway/wallpaper
cp /path/to/your/wallpaper.png ~/.config/sway/wallpaper/
```

Then update the `output * bg` line in `~/.config/sway/config`:

```conf
output * bg ~/.config/sway/wallpaper/your-wallpaper.png fill
```

Theme switcher (`Super+Shift+T`) will swap wallpapers automatically if each theme's `theme.conf` references the correct filename.
```

### Step 12 — Stale package tracking

Create `archive/rework/stale-packages.txt` listing packages that are now superseded but not yet removed from `packages/apt.txt`:

```
# Stale packages — superseded by Phase 4 replacements
# Remove from packages/apt.txt after system stabilization
#
# Package         | Replaced by    | Reason
copyq             | cliphist       | Wayland-native clipboard history
copyq-plugins     | cliphist       | Wayland-native clipboard history
fuzzel            | rofi           | App launcher (rofi enables theme switcher, power menu, cheatsheet)
```

These packages stay in `apt.txt` until the system is confirmed stable, then are removed in a follow-up cleanup commit.

### Step 13 — Update README.md keybindings

Replace the entire keybindings section (lines 66-93) with the new keybindings:

```markdown
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
```

---

## Deployment Order

```
install.sh
  ├── 20-packages.sh reads packages/apt.txt → installs sway, rofi, grim, slurp, cliphist, kitty, etc.
  ├── make all → deploys sway config, kitty config, rofi config, waybar, scripts, themes
  └── 60-lockdown.sh → deploys updated clipboard-clear.sh, seal_lib.py, allowlist.sh, sudoers
```

No code changes to `install.sh`, `20-packages.sh`, or `60-lockdown.sh`. Only:
- Text file additions (`packages/apt.txt`)
- New directories (`dotfiles/sway/themes/`, `dotfiles/kitty/`, `dotfiles/rofi/`, etc.)
- Makefile target update (app list in `dotfiles` target)
- Lockdown file updates (6 files: clipboard-clear.sh, seal_lib.py, allowlist.sh, seal.py, sem.py, sudoers)
- Archive of old files (`archive/rework/`)
- Stale package tracking (`archive/rework/stale-packages.txt`)

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
- `dotfiles/sway/themes/catppuccin/theme.conf` + `colors.conf`
- `dotfiles/sway/themes/doom_one/theme.conf` + `colors.conf`
- `dotfiles/sway/themes/dracula/theme.conf` + `colors.conf`
- `dotfiles/sway/themes/everforest/theme.conf` + `colors.conf`
- `dotfiles/sway/themes/github_dark/theme.conf` + `colors.conf`
- `dotfiles/sway/themes/gruvbox/theme.conf` + `colors.conf`
- `dotfiles/sway/themes/kanagawa/theme.conf` + `colors.conf`
- `dotfiles/sway/themes/monokai/theme.conf` + `colors.conf`
- `dotfiles/sway/themes/moonfly/theme.conf` + `colors.conf`
- `dotfiles/sway/themes/nord/theme.conf` + `colors.conf`
- `dotfiles/sway/themes/retro/theme.conf` + `colors.conf`
- `dotfiles/sway/themes/rose_pine_moon/theme.conf` + `colors.conf`
- `dotfiles/kitty/kitty.conf`
- `dotfiles/kitty/current-theme.conf`
- `LICENSES/GPL-2.0.txt`
- `archive/rework/stale-packages.txt`

**Archive** (move to `archive/rework/`)
- `dotfiles/sway/sway_config` → `archive/rework/sway_config`
- `dotfiles/copyq/` → `archive/rework/copyq/`
- `dotfiles/fuzzel/` → `archive/rework/fuzzel/`
- `dotfiles/waybar/style.css` → `archive/rework/waybar-style.css`
- `dotfiles/waybar/mocha.css` → `archive/rework/waybar-mocha.css`
- `dotfiles/waybar/scripts/launch-waybar.sh` → `archive/rework/launch-waybar.sh`
- `dotfiles/waybar/waybar_config.json` → `archive/rework/waybar_config.json`

**Modify**
- `packages/apt.txt` (add ~45 new packages, keep stale ones for now)
- `Makefile` (update `dotfiles` target app list, symlinks)
- `lockdown/lib/clipboard-clear.sh` (add cliphist)
- `lockdown/allowlist/scripts/seal_lib.py` (remove CopyQ, use cliphist)
- `lockdown/allowlist/scripts/allowlist.sh` (remove CopyQ logic, use cliphist)
- `lockdown/allowlist/scripts/seal.py` (update UI string: copyq → cliphist)
- `lockdown/allowlist/scripts/sem.py` (update UI string: copyq → cliphist)
- `lockdown/sudoers/99-mike-tools` (replace copyq with cliphist)
- `dotfiles/waybar/scripts/clear-clipboard.sh` (replace CopyQ eval)
- `dotfiles/waybar/scripts/toggle-nmtui.sh` (use $TERMINAL)
- `manual_work.md` (update CopyQ/fuzzel references)
- `README.md` (add credits, wallpaper setup, update keybindings)

**NOT modified**
- `install.sh` (orchestrator unchanged)
- `lib/20-packages.sh` (module unchanged — reads text files)
- `lib/60-lockdown.sh` (orchestrator unchanged — deploys files)
- `dotfiles/foot/foot.ini` (stays as fallback terminal — moved to `dotfiles/sway/foot/`)
- `dotfiles/waybar/scripts/launch-browser.sh` (stays as-is, used by `$mod+Shift+g`)

---

## Testing

1. Run `bash lib/20-packages.sh` — verify new packages install
2. Verify cliphist installed: `which cliphist` and `cliphist version`
3. Verify kitty installed: `which kitty`
4. Run `make all` — verify new sway config lands in `~/.config/sway/`
5. Verify theme structure: `ls ~/.config/sway/themes/` shows 12 themes
6. Verify kitty config: `ls ~/.config/kitty/` shows kitty.conf and current-theme.conf
7. Verify rofi config: `ls ~/.config/sway/rofi/` shows config.rasi, keybinds.rasi, power.rasi
8. Verify symlinks: `ls -la ~/.config/foot` and `ls -la ~/.config/gtklock` point to sway subdirs
9. Log out and log back into sway
10. Test `Super+Space` — rofi launcher should appear
11. Test `Super+Shift+T` — theme switcher should show 12 themes
12. Test `Super+Shift+S` — screenshot should work (grim+slurp)
13. Test `Super+V` — cliphist history should appear via rofi
14. Test `Super+X` — power menu should appear
15. Test `Super+/` — keybind cheatsheet should appear
16. Test `Super+Return` — kitty terminal should launch
17. Verify waybar displays correctly with theme colors
18. Verify swaync notifications work
19. Run lockdown: `sudo bash lib/60-lockdown.sh`
20. Test seal flow: verify clipboard-clear adapter uses cliphist
21. Verify no hardcoded `copyq` in deployed lockdown files: `grep -r copyq /opt/allowlist/ /usr/local/lib/lockdown/`
22. Verify no hardcoded `copyq` in repo: `grep -r copyq --include='*.sh' --include='*.py' --include='*.md' --include='Makefile' . | grep -v archive/ | grep -v '.git/'`
23. Verify archived files exist: `ls archive/rework/` shows sway_config, copyq/, fuzzel/, waybar-*.css, launch-waybar.sh

---

## Revert plan

If the merge causes issues:

1. Move archived files back from `archive/rework/` to their original locations
2. Remove new files: `dotfiles/sway/themes/`, `dotfiles/kitty/`, `dotfiles/sway/rofi/`, etc.
3. Revert `packages/apt.txt` changes
4. Revert `lockdown/lib/clipboard-clear.sh`, `seal_lib.py`, `allowlist.sh`, `sudoers`
5. Revert `Makefile` dotfiles target
6. Run `make all && sudo bash lib/60-lockdown.sh`

All changes are in tracked files — archived originals can be restored with `cp -r`.
