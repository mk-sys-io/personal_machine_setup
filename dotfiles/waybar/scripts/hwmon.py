#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import sys

try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False


def _nvidia_query(fields: str) -> list[str] | None:
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=" + fields, "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0:
            return None
        return [x.strip() for x in result.stdout.strip().split(",")]
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None


def _cpu_temp_class(temp: int) -> str:
    if temp >= 90:
        return "critical"
    if temp >= 80:
        return "warning"
    return "normal"


def _gpu_temp_class(temp: int) -> str:
    if temp >= 85:
        return "critical"
    if temp >= 80:
        return "warning"
    return "normal"


def cpu_temp() -> dict[str, str]:
    if not HAS_PSUTIL:
        return {
            "text": "<span color='#D29922'>\U0000f2c9</span> ?",
            "tooltip": "CPU module unavailable \u2014 install psutil (`pip install psutil`)",
            "class": "error",
        }

    temps = psutil.sensors_temperatures()
    pkg_temp: int | None = None
    core_temps: list[tuple[str, float]] = []

    for key in ("coretemp", "k10temp"):
        if key not in temps:
            continue
        for entry in temps[key]:
            label = entry.label or ""
            if label in ("Package id 0", "Tctl"):
                pkg_temp = int(entry.current)
            elif label.startswith("Core ") or label.startswith("Tccd"):
                core_temps.append((label, entry.current))
        break

    if pkg_temp is None:
        for entries in temps.values():
            for entry in entries:
                if entry.current:
                    pkg_temp = int(entry.current)
                    break
            if pkg_temp is not None:
                break

    if pkg_temp is None:
        return {
            "text": "<span color='#D29922'>\U0000f2c9</span> ?",
            "tooltip": "No CPU temperature sensors found",
            "class": "error",
        }

    tip_lines = [f"Package: {pkg_temp}\u00b0C"]
    for label, temp in core_temps:
        tip_lines.append(f"{label}: {int(temp)}\u00b0C")

    return {
        "text": f"<span color='#D29922'>\U0000f2c9</span> {pkg_temp}\u00b0",
        "tooltip": "\n".join(tip_lines),
        "class": _cpu_temp_class(pkg_temp),
    }


def gpu_spec() -> dict[str, str]:
    data = _nvidia_query("temperature.gpu,power.draw,fan.speed,utilization.gpu,memory.used,memory.total")
    if data is None or len(data) < 6:
        return {
            "text": "<span color='#D29922'>\U0000f2db</span> ?",
            "tooltip": "GPU monitoring unavailable \u2014 check NVIDIA driver / nvidia-smi",
            "class": "error",
        }

    temp = int(data[0])
    power = data[1]
    fan = data[2]
    util = int(data[3])
    vram_used = int(data[4])
    vram_total = int(data[5])
    vram_pct = (vram_used / vram_total * 100) if vram_total > 0 else 0

    def mib_to_gib(mib: int) -> str:
        return f"{mib / 1024:.1f}"

    tip = f"GPU: {temp}\u00b0C"
    if power and power != "[N/A]":
        tip += f"\nPower: {power}W"
    if fan and fan != "[N/A]":
        tip += f"\nFan: {fan}%"
    tip += f"\nUtilization: {util}%"
    tip += f"\nVRAM: {mib_to_gib(vram_used)} / {mib_to_gib(vram_total)} GiB ({vram_pct:.0f}%)"

    return {
        "text": f"<span color='#D29922'>\U0000f2db</span> {temp}\u00b0",
        "tooltip": tip,
        "class": _gpu_temp_class(temp),
    }


def main() -> None:
    module = sys.argv[1] if len(sys.argv) > 1 else ""
    match module:
        case "cpu-temp":   data = cpu_temp()
        case "gpu-spec":   data = gpu_spec()
        case _:
            data = {
                "text": "?",
                "tooltip": "Usage: hwmon.py {cpu-temp|gpu-spec}",
                "class": "error",
            }
    print(json.dumps(data))


if __name__ == "__main__":
    main()
