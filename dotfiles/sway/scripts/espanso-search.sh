#!/bin/bash
# Toggle espanso search bar: kill the open search UI, or open it.
if pgrep -f "espanso modulo search" >/dev/null 2>&1; then
    pkill -f "espanso modulo search"
else
    espanso cmd search &
fi