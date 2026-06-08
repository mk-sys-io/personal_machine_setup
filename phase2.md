+ Phase one was more about exploring the feasibility and limitation of the system. In this document, I will be describing a minimal viable design to implement. The idea is that I want to start using this system as soon as possible then figure new features as we go.

# Color Palette

A balanced, low-contrast, slate-and-deep-blue environment for reduced ocular strain during extended deep-work intervals.

| Role | Hex | Usage |
|------|-----|-------|
| Muted Grayish Blue | `#32344a` | Inactive window borders, structural dividers |
| Deep Dark Blue | `#16161e` | Waybar background, system background tint |
| Accent Slate Blue | `#7aa2f7` | Active workspace indicator, focused text elements |
| Dimmed Slate | `#444b6a` | Inactive workspace markings, secondary text |
| Crisp Foreground | `#c0caf5` | Primary typography, clock text, standard icons |

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
window#waybar {
    background-color: #16161e;
    color: #c0caf5;
    font-family: "JetBrains Mono", "SF Mono", monospace;
    font-size: 10pt;
    font-weight: 500;
    transition-property: none;
}

#workspaces button {
    padding: 0px 14px;
    color: #444b6a;
    background-color: transparent;
    border-bottom: 2px solid transparent;
}

#workspaces button.focused {
    color: #7aa2f7;
    background-color: #1a1b26;
    border-bottom: 2px solid #7aa2f7;
}

#clock {
    color: #c0caf5;
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
| client.focused | #32344a | #16161e | #c0caf5 | #7aa2f7 |
| client.focused_inactive | #16161e | #16161e | #444b6a | #16161e |
| client.unfocused | #16161e | #16161e | #444b6a | #16161e |
| client.urgent | #7aa2f7 | #7aa2f7 | #16161e | #7aa2f7 |

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

## Helper Utility
+ A utility triggered by a bash alias such as `help` that shows all the available keybindings mapped to the corresponding keys without the hassle of opening all the configuration files & navigating unnecessary info.
