#!/bin/bash

pct=$(brightnessctl get 2>/dev/null)
max=$(brightnessctl max 2>/dev/null)

if [[ -n "$pct" && -n "$max" && "$max" -gt 0 ]]; then
  val=$((pct * 100 / max))
  echo "{\"text\": \"BRT $val%\", \"class\": \"on\"}"
else
  echo "{\"text\": \"BRT ?\", \"class\": \"off\"}"
fi
