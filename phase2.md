+ Phase one was more about exploring the feasibility and limitation of the system. In this document, I will be describing a minimal viable design to implement. The idea is that I want to start using this system as soon as possible then figure new features as we go.

# Color Palette

Catppuccin Mocha — a warm, low-contrast palette for reduced ocular strain during extended deep-work intervals.

| Variable | Hex | Usage |
|----------|-----|-------|
| `@base`   | `#1e1e2e` | Desktop background, window backgrounds |
| `@mantle` | `#181825` | Waybar background, CopyQ list background |
| `@text`   | `#cdd6f4` | Primary typography, clock text, standard icons |
| `@blue`   | `#89b4fa` | Active workspace indicator, selection highlights |
| `@surface1` | `#45475a` | Window borders, structural dividers |

All colors defined in `.config/waybar/mocha.css` and sourced from the official Catppuccin Mocha palette.

# Waybar

## Bar Geometry
- Position: top edge of the screen
- Height: 28px (fixed uniform profile)
- Margins: 0px top, 0px sides (full width bleed)
- Border: none (seamless panel alignment)

## Typography & Padding
- Primary font: "JetBrains Mono", "SF Mono", monospace
- Font weight: 500 (SemiBold)
- Font size: 10pt
- Internal module padding: horizontal 14px, vertical 0px
- Module spacing: 0px

## Structural Alignment
- Left section: Workspaces (minimal numeric or dot identifiers)
- Center section: Isolated central clock module (layout anchor)
- Right section: Static status indicators — volume level, battery level, wifi connection status

## CSS Definitions

```css
@import "mocha.css";

window#waybar {
    background-color: @mantle;
    color: @text;
    font-family: "JetBrains Mono", "SF Mono", monospace;
    font-size: 14pt;
    font-weight: 500;
    transition-property: none;
    margin: 9px 13px 0px 13px;
    border-radius: 12px;
}

#workspaces button {
    padding: 0px 14px;
    color: @surface1;
    background-color: transparent;
    border-bottom: 2px solid transparent;
}

#workspaces button.focused {
    color: @blue;
    background-color: @base;
    border-bottom: 2px solid @blue;
}

#clock {
    color: @text;
    font-weight: 600;
    padding: 0px 18px;
    letter-spacing: 0.5px;
}
```

# Sway

## Description

+ Remove the keybinding to utilities such as btop, calendar. Keep terminal, nmtui, and clipboard
+ Cap window generation in one workspace to two and make the third window start in a new workspace — **DEFERRED** (see Decision Log)
+ Apply the slate-and-deep-blue palette from the style guide to window borders and background

## Window Borders & Canvas Rules
- Border width: 1px uniform
- Titlebars: disabled globally
- Gaps (inner): 12px
- Gaps (outer): 14px

## Sway Color Palette
| Class | Border | Background | Text | Indicator |
|-------|--------|------------|------|-----------|
| client.focused | #45475a | #1e1e2e | #cdd6f4 | #89b4fa |
| client.focused_inactive | #1e1e2e | #1e1e2e | #6c7086 | #1e1e2e |
| client.unfocused | #1e1e2e | #1e1e2e | #6c7086 | #1e1e2e |
| client.urgent | #fab387 | #fab387 | #181825 | #fab387 |

# Decision Log

## Clipboard deletion — RESOLVED

Clipboard history management migrated from `clipman` (basic picker, no interactive deletion) to `CopyQ` (full Qt GUI with built-in item deletion via right-click or Delete key, plus clear-all via `$mod+Shift+v`). Replacing clipman was the proper solution — CopyQ provides native interactive deletion, Catppuccin Mocha theming, and `wlr-data-control` protocol support on Wayland.

## Auto-move to new workspace / window cap — DEFERRED

The idea was: cap windows at 2 per workspace, and when a third is opened, auto-move it to a new workspace.

**Why this is technically hard:**

Sway has no built-in `max_windows_per_workspace` setting. Implementing it requires an IPC daemon that subscribes to `window::new` events via `swaymsg -m subscribe`, counts windows on the current workspace, and calls `swaymsg move container to workspace number N`. This approach has several problems:

- **Race conditions**: Sway's IPC is asynchronous. By the time the script reacts to a `window::new` event and issues a `move` command, the window state may have already changed (e.g., the user closed a window, changing the count).
- **No reconnect**: `swaymsg -m subscribe` reads JSON events from stdin with no built-in reconnection if the connection drops, making a persistent daemon fragile.
- **Complexity cost**: Adds a background daemon with state management, contrary to the project's minimalism goal. Debugging edge cases (floating windows, dialogs, fullscreen) would consume more time than the feature saves.
- **Edge cases**: What counts as a "window"? Popups, dialogs, scratchpad entries? Does moving a tiling window mid-session disrupt the user's mental model?

**Recommendation:** Defer indefinitely. In practice, manually keeping ≤2 windows per workspace works well — Sway's workspace switching is fast enough that splitting across workspaces manually adds negligible friction.

# What to add

## Helper Utility — RESOLVED
+ A keybinding reference system implemented via `.config/scripts/help.sh`, deployed to `/usr/local/bin/help` — opens `keybindings.md` in a floating foot terminal (1000x500) rendered with `glow --pager` for styled table display. Triggered by `$mod+F1` (toggle — second press closes) or simply `help`. No alias needed — `/usr/local/bin/` is in `$PATH`. (See docs/utility-scripts.md.)

  **Note:** Click-away dismissal (clicking outside the panel to close) is not feasible here. Sway does not support click-away for standard XDG windows (foot), only for wlr-layer-shell surfaces (fuzzel). Close via `$mod+F1` toggle or `q` inside the terminal.
