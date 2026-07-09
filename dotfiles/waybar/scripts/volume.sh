#!/bin/bash

raw=$(wpctl get-volume @DEFAULT_AUDIO_SINK@)
vol=$(echo "$raw" | awk '{printf "%d", $2 * 100}')

if echo "$raw" | grep -q "MUTED"; then
  echo "{\"text\": \"VOL $vol%\", \"class\": \"muted\"}"
else
  echo "{\"text\": \"VOL $vol%\", \"class\": \"on\"}"
fi
