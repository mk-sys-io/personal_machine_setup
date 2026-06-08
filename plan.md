# Goal

Create a terminal based, keyboard driven environment that reduces distraction & cognitive load.

### Sub-goals

+ Create a comprehensive setup script
+ Test on bare metal

## Notes

+ This setup is built on the philosophy of digital minimalism
+ Currently I am testing this setup in fedora server VM
+ Battery section configs do not work inside a VM
+ See waybar error logs using `waybar &`, reload sway using `swaymsg reload`

# Phase 1

## Scope
+ Implement a sleek, dark and modern interface for fedora server.
+ Add essential utilities such as calendar, wifi connection, volume, luminosity, clipboard

## Components
+ Sway
+ Fuzzel
+ Waybar
+ Foot Terminal
+ Swaybg
+ Utilities

## Features

+ **Zero Wallpapers**: Setting a solid #1e1e2e or black color via swaybg entirely removes the visual noise behind your windows. When you look at your screen, your eyes go directly to your terminal buffers or documentation.

+ **Native Window Borders**: Instead of a complex task switcher, window borders act as the solitary visual indicator of focus. Active windows have a distinct slate grey edge (#45475a); inactive ones melt away into the background.

+ **Standard Waybar**: The default system Waybar loads immediately, outputting your battery status, network state, time, and active workspaces cleanly. There's no need to spend hours coding custom CSS scripts just to view system statistics.

# Tools

| Utility | Purpose | Config File(s) |
|---|---|---|
| **sway** | Tiling Wayland compositor (window manager) | `sway_config`, `.bashrc_config` |
| **waybar** | Status bar (workspaces, clock, network, battery) | `sway_config`, `waybar_config.json`, `style.css` |
| **foot** | Wayland-native terminal emulator | `sway_config`, `foot.ini` |
| **fuzzel** | Application launcher / command runner (click-away dismisses) | `sway_config`, `fuzzel.ini` |
| **nmtui** | Wi-Fi connection manager (TUI) | `sway_config`, `scripts/toggle-nmtui.sh` |
| **network-manager** | Network management daemon | — |
| **wl-clipboard** | Clipboard (wl-paste / wl-copy) | `sway_config` |
| **copyq** | Clipboard history manager (Qt GUI, Catppuccin Mocha theme) | `sway_config`, `copyq.conf` |
| **ydotool** | Wayland keystroke injection (auto-paste from CopyQ) | `sway_config` |
| **brightnessctl** | Backlight brightness control | `scripts/brightness.sh`, `sway_config` |
| **wireplumber** | Audio session manager (volume control) | `scripts/volume.sh`, `sway_config` |
| **fonts-jetbrains-mono** | Monospace terminal font | `foot.ini`, `style.css`, `sway_config` |
| **foot-terminfo** | Terminal type definitions for foot | `foot.ini` |

| **bash** | Login shell with auto-launch Sway on TTY1 | `.config/bashrc` |
| **libglib2.0-bin** | gsettings CLI (set dark mode color scheme) | `sway_config` |

# Keybindings

| Key | Utility | Action | Changes |
|---|---|---|---|
| `$mod+Return` | foot | Launch terminal | — |
| `$mod+d` | fuzzel | Toggle app launcher | Switched from wofi to fuzzel (native click-away dismiss via wlr-layer-shell) |
| `$mod+Shift+q` | Sway kill | Close focused window | — |
| `$mod+Shift+e` | swaymsg | Exit Sway session | — |
| `$mod+Shift+c` | Sway reload | Reload sway config | — |
| `$mod+n` | nmtui | Toggle Wi-Fi panel | — |
| `$mod+v` | copyq | Toggle clipboard history window | Switched from clipman+fuzzel to CopyQ (Catppuccin Mocha themed) |
| `$mod+Shift+v` | copyq eval | Clear all clipboard history | New — wraps `copyq eval` to remove all items |
| `$mod+Shift+g` | launch-browser | Launch minimal browser (Brave) | Switched from Chromium to Brave |
| `$mod+Escape` | Sway workspace | Jump to next workspace | — |
| `$mod+←/↓/↑/→` | Sway focus | Move focus directionally | — |
| `$mod+Shift+←/↓/↑/→` | Sway move | Move window directionally | — |
| `$mod+1-5` | Sway workspace | Switch to workspace N | — |
| `$mod+Shift+1-5` | Sway container | Move window to workspace N | — |
| `$mod+f` | Sway fullscreen | Toggle fullscreen | — |
| `$mod+h` / `$mod+g` | Sway layout | Horizontal / vertical split | — |
| `XF86MonBrightnessUp` | brightnessctl / waybar | Brightness +5% + refresh indicator | Added `pkill -SIGRTMIN+3 waybar` for instant update |
| `XF86MonBrightnessDown` | brightnessctl / waybar | Brightness -5% + refresh indicator | Added `pkill -SIGRTMIN+3 waybar` for instant update |
| `XF86AudioRaiseVolume` | wpctl / waybar | Volume +5% + refresh indicator | Added `pkill -SIGRTMIN+2 waybar` for instant update |
| `XF86AudioLowerVolume` | wpctl / waybar | Volume -5% + refresh indicator | Added `pkill -SIGRTMIN+2 waybar` for instant update |

# Decision Log

## Phase 1 decisions
+ Do not use vim based bindings
+ Launch sway automatically via bash config
+ Strip waybar from any interactive utilities
+ Waybar should be used for static info, any interactive menu should stripped down and left to key bindings triggering utilities


## Design decisions
+ **Abandon Fedora Everything ISO Idea**: This is overkill & time consuming at this stage when there are more pressing matters at hand. Debloating Plasma offers no clean performance benefits. The manual package selection could lead to problems (specifically hardware drivers)
+ **Stick to Zed IDE over VS Code**: The learning curve that comes with learning vim cannot be tolerated at the moment. I have found out that you need to be a decent fast typer to use vim effectively which I currently lack. Redesigning VS code for minimalism is counterintuitive when Zed offers minimalism out of the box and good terminal integration.
+ **Using Pi hole, AdGuard on the host machine is pointless**: Installing these tools then redirecting them to be the DNS resolver on the same machine they deployed on results in configurations errors

## Phase 2 decisions
+ **Migrate kitty → foot**: Kitty requires OpenGL 3.3+ GPU acceleration and fails on minimal Debian installs (VMs, headless, no GPU drivers). Foot is Wayland-native, CPU-rendered, lightweight, and reliably runs on any hardware. All configs, scripts, and keybindings were updated accordingly.
+ **Dark mode without a DE**: Chromium and GTK apps on Sway need `gsettings` to set the dark color scheme (`org.gnome.desktop.interface color-scheme prefer-dark`). Requires `libglib2.0-bin` (~365 KB) for the CLI and the already-installed `dconf-service` for persistence. The gsettings command is run both in `install.sh` and on every Sway start via `exec` in the sway config, ensuring it applies even on first boot before the install script runs.

## Phase 2 decisions (continued)

+ **Pivot from Chromium to Brave**: Chromium was removed due to Debian wrapper complexity (`/usr/bin/chromium` shell script, `/etc/chromium.d/` flag injection) causing unexpected behavior with extension force-install and no clean way to control flags. Brave works out of the box on Wayland, has enterprise policies to debloat every non-essential feature (Rewards, Wallet, VPN, Leo AI, Tor, News, Talk, telemetry, etc.), and Brave Shields replaces Privacy Badger for tracker blocking. Privacy Badger dropped from extension force-list (2 extensions remain: uBlock Origin + Bitwarden). Brave installed via official apt repo with GPG key pinning.

+ **Migrate from wofi to fuzzel**: `hide_on_focus_loss=true` in wofi did not reliably dismiss the launcher when clicking away on Sway/Wayland, especially with touchpad input. Fuzzel uses the wlr-layer-shell protocol natively and handles click-away dismissal properly without configuration hacks. Replaced wofi for $mod+d (app launcher). Fuzzel config stored at `.config/fuzzel/fuzzel.ini` — minimal setup (font, lines, width, prompt). Wofi configs and package removed.

+ **Migrate clipman → CopyQ for clipboard history**: Clipman provided a basic history with `wl-paste --watch` + `clipman pick | fuzzel --dmenu` but lacked interactive deletion. CopyQ brings a full Qt GUI with built-in item deletion (right-click → Remove or Delete key), clear-all via CLI (`copyq eval`), native `wlr-data-control` protocol support on Wayland, and official Catppuccin Mocha Blue theme. Toggle bound to `$mod+v`, clear-all bound to `$mod+Shift+v`. CopyQ configured with 40-item limit, hidden menu/tab/toolbars for minimal look, and no system tray icon (interaction only via keybindings). Auto-paste on Wayland (Enter → paste to previous window) requires ydotool — downloaded as pre-built binary from GitHub releases (not in Debian repos). ydotoold started as `exec ydotoold` in sway config. CopyQ's Wayland Support command must be imported manually via File → Commands → Import (documented in `manual_work.md`).

## Pitfalls
+ Do not use `line-height` CSS property or `!important` keyword: they trigger parsing errors

# Future Ideas
+ Tools: tmux
+ backups, locking passwords behind time based locks
+ Implement Zsh
+ Browser: pinned app mode with --app=https://example.com for site-isolated kiosk frames
