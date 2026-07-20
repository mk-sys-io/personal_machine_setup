#!/bin/bash
# Hardware monitor for waybar — CPU temp, GPU temp, VRAM usage

cpu_temp=""
gpu_temp=""
vram_used=""
vram_total=""

# CPU Package temp — scan hwmon labels for "Package id 0"
for d in /sys/class/hwmon/hwmon*/temp*_input; do
    label_file="${d%_input}_label"
    if [ -f "$label_file" ] && [ "$(cat "$label_file" 2>/dev/null)" = "Package id 0" ]; then
        cpu_temp=$(( $(cat "$d") / 1000 ))
        break
    fi
done

# NVIDIA GPU — temp + VRAM
if nvidia-smi &>/dev/null; then
    IFS=', ' read -r gpu_temp vram_used vram_total <<< "$(nvidia-smi --query-gpu=temperature.gpu,memory.used,memory.total --format=csv,noheader 2>/dev/null | tr -d 'MiB')"
    vram_used=$(echo "$vram_used" | tr -d ' ')
    vram_total=$(echo "$vram_total" | tr -d ' ')
fi

# Build compact inline text
text="<span color='#D29922'>󰋚</span>"
[ -n "$cpu_temp" ] && text="$text ${cpu_temp}°"
[ -n "$gpu_temp" ] && text="$text ${gpu_temp}°"

# Build tooltip
tooltip="CPU:  ${cpu_temp:-N/A}°C (Package)"
tooltip="$tooltip\nGPU: ${gpu_temp:-N/A}°C"
tooltip="$tooltip\nVRAM: ${vram_used:-0} / ${vram_total:-0} MiB"

# CSS class based on CPU and GPU temps
class="normal"
critical=false
warning=false

if [ -n "$cpu_temp" ] && [ "$cpu_temp" -ge 90 ]; then
    critical=true
elif [ -n "$cpu_temp" ] && [ "$cpu_temp" -ge 80 ]; then
    warning=true
fi

if [ -n "$gpu_temp" ] && [ "$gpu_temp" -ge 85 ]; then
    critical=true
elif [ -n "$gpu_temp" ] && [ "$gpu_temp" -ge 80 ]; then
    warning=true
fi

if $critical; then
    class="critical"
elif $warning; then
    class="warning"
fi

printf '{"text":"%s","tooltip":"%s","class":"%s"}\n' "$text" "$tooltip" "$class"
