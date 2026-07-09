#!/bin/bash

if pgrep -f "[f]oot.*nmtui" > /dev/null 2>&1; then
    pkill -f "[f]oot.*nmtui"
else
    foot nmtui
fi
