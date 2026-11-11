#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import sys

try:
    import psutil
except ImportError:
    psutil = None  # type: ignore[assignment]


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


_NA_TOKENS = {"", "N/A", "[N/A]", "[Not Supported]", "[Unknown Error]"}


def _num(value: str) -> float | None:
    """Parse an nvidia-smi numeric field; None when the field is unavailable."""
    if value.strip() in _NA_TOKENS:
        return None
    try:
        return float(value)
    except ValueError:
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
    if psutil is None:
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


def hw_mon() -> dict[str, str]:
    cpu = cpu_temp()
    gpu = gpu_spec()

    lines = []
    if "Package" not in cpu.get("tooltip", ""):
        lines.append("CPU: unavailable")
    else:
        lines.append(cpu["tooltip"])
    lines.append("")
    if "GPU" not in gpu.get("tooltip", ""):
        lines.append("GPU: unavailable")
    else:
        lines.append(gpu["tooltip"])

    classes = [cpu.get("class", "normal"), gpu.get("class", "normal")]
    if "critical" in classes:
        cls = "critical"
    elif "warning" in classes:
        cls = "warning"
    elif "error" in classes:
        cls = "error"
    else:
        cls = "normal"

    text = cpu.get("text", "?")
    return {
        "text": text,
        "tooltip": "\n".join(lines),
        "class": cls,
    }


def gpu_spec() -> dict[str, str]:
    data = _nvidia_query("temperature.gpu,power.draw,fan.speed,utilization.gpu,memory.used,memory.total")
    if data is None or len(data) < 6:
        return {
            "text": "<span color='#D29922'>\U0000f2db</span> ?",
            "tooltip": "GPU monitoring unavailable \u2014 check NVIDIA driver / nvidia-smi",
            "class": "error",
        }

    temp = _num(data[0])
    if temp is None:
        return {
            "text": "<span color='#D29922'>\U0000f2db</span> ?",
            "tooltip": "GPU temperature unavailable \u2014 nvidia-smi returned no reading",
            "class": "error",
        }
    temp = int(temp)
    power = _num(data[1])
    fan = _num(data[2])
    util = int(_num(data[3]) or 0)
    vram_used = _num(data[4])
    vram_total = _num(data[5])
    vram_pct = (vram_used / vram_total * 100) if vram_used is not None and vram_total else 0

    tip = f"GPU: {temp}\u00b0C"
    if power is not None:
        tip += f"\nPower: {power:.0f}W"
    if fan is not None:
        tip += f"\nFan: {fan:.0f}%"
    tip += f"\nUtilization: {util}%"
    if vram_used is not None and vram_total:
        tip += f"\nVRAM: {vram_used / 1024:.1f} / {vram_total / 1024:.1f} GiB ({vram_pct:.0f}%)"

    return {
        "text": f"<span color='#D29922'>\U0000f2db</span> {temp}\u00b0",
        "tooltip": tip,
        "class": _gpu_temp_class(temp),
    }


def main() -> None:
    module = sys.argv[1] if len(sys.argv) > 1 else ""
    match module:
        case "hw-mon":
            data = hw_mon()
        case _:
            data = {
                "text": "?",
                "tooltip": "Usage: hwmon.py {hw-mon}",
                "class": "error",
            }
    print(json.dumps(data))


if __name__ == "__main__":
    main()
