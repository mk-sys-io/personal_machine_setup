#!/bin/bash
HELP_TITLE="keybindings"

# Toggle: kill if already running
if pgrep -f "foot --title=$HELP_TITLE" > /dev/null 2>&1; then
    pkill -f "foot --title=$HELP_TITLE"
    exit 0
fi

# Option B: glow --pager (built-in TUI, proper table rendering, stays open)
if command -v glow &>/dev/null; then
    foot --title="$HELP_TITLE" -e glow --pager ~/linux_setup/keybindings.md
else
    # Option A fallback: glow stdout piped to less
    foot --title="$HELP_TITLE" -e sh -c "glow ~/linux_setup/keybindings.md | less -R"
fi
