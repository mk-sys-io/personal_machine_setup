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
+ Wofi
+ Waybar
+ Foot Terminal
+ Swaybg
+ Utilities

## Features

+ **Zero Wallpapers**: Setting a solid #1a1b26 or black color via swaybg entirely removes the visual noise behind your windows. When you look at your screen, your eyes go directly to your terminal buffers or documentation.

+ **Native Window Borders**: Instead of a complex task switcher, window borders act as the solitary visual indicator of focus. Active windows have a distinct slate grey edge (#4c566a); inactive ones melt away into the background.

+ **Standard Waybar**: The default system Waybar loads immediately, outputting your battery status, network state, time, and active workspaces cleanly. There's no need to spend hours coding custom CSS scripts just to view system statistics.

# Tools

| Utility | Purpose | Config File(s) |
|---|---|---|
| **sway** | Tiling Wayland compositor (window manager) | `sway_config`, `.bashrc_config` |
| **waybar** | Status bar (workspaces, clock, network, battery) | `sway_config`, `waybar_config.json`, `style.css` |
| **foot** | Wayland-native terminal emulator | `sway_config`, `foot.ini` |
| **wofi** | Application launcher / command runner | `sway_config` |
| **nmtui** | Wi-Fi connection manager (TUI) | `sway_config`, `scripts/toggle-nmtui.sh` |
| **network-manager** | Network management daemon | — |
| **wl-clipboard** | Clipboard (wl-paste / wl-copy) | `sway_config` |
| **clipman** | Clipboard history manager (daemon + picker) | `sway_config` |
| **brightnessctl** | Backlight brightness control | `scripts/brightness.sh`, `sway_config` |
| **wireplumber** | Audio session manager (volume control) | `scripts/volume.sh`, `sway_config` |
| **fonts-jetbrains-mono** | Monospace terminal font | `foot.ini`, `style.css`, `wofi-style.css`, `sway_config` |
| **foot-terminfo** | Terminal type definitions for foot | `foot.ini` |

| **bash** | Login shell with auto-launch Sway on TTY1 | `.bashrc_config` |

# Keybindings

| Key | Utility | Action | Changes |
|---|---|---|---|
| `$mod+Return` | foot | Launch terminal | — |
| `$mod+d` | wofi | Toggle app launcher | Switched from plain launch to toggle with `pkill wofi \|\| wofi --show run` |
| `$mod+Shift+q` | Sway kill | Close focused window | — |
| `$mod+Shift+e` | swaymsg | Exit Sway session | — |
| `$mod+Shift+c` | Sway reload | Reload sway config | — |
| `$mod+n` | nmtui | Toggle Wi-Fi panel | — |
| `$mod+v` | clipman / wofi | Toggle clipboard history | — |
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
+ **Stick to Zed IDE over VS Code**: The learning Curve that comes with learning vim cannot be tolerated at the moment. I have found out that you need to be a decent fast typer to use vim effectively which I currently lack. Redesigning VS code for minimalism is counterintuitive when Zed offers minimalism out of the box and good terminal integration.
+ **Using Pi hole, AdGuard on the host machine is pointless**: Installing these tools then redirecting them to be the DNS resolver on the same machine they deployed on results in configurations errors

## Phase 2 decisions
+ **Migrate kitty → foot**: Kitty requires OpenGL 3.3+ GPU acceleration and fails on minimal Debian installs (VMs, headless, no GPU drivers). Foot is Wayland-native, CPU-rendered, lightweight, and reliably runs on any hardware. All configs, scripts, and keybindings were updated accordingly.

## Pitfalls
+ Do not use `line-height` CSS property or `!important` keyword: they trigger parsing errors

# Future Ideas
+ Tools: Git, tmux
+ Fix problems like lack of a clock, wifi connection panel and clipboard history management
+ Timeshift, backups, locking passwords behind time based locks
+ Implement Zsh
