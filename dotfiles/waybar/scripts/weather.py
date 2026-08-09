#!/usr/bin/env python3
"""weather.py — minimal Waybar weather module.

Fetches current weather from Open-Meteo and geolocates via ipwhois.io.
Stdlib only. Runs as a waybar custom module with return-type json.

Geolocation strategy (avoids hammering the geo API):
  - Cache last-known coords to ~/.cache/waybar-weather/location.json
  - Re-query ipwhois.io on every boot (boot_id changed) or when the
    cache is older than 12 hours
  - Fall back to stale cache, then to WEATHER_FALLBACK_* (env vars, then
    ~/.config/linux_setup/config.env, then Tunis defaults)
"""

from __future__ import annotations

import json
import os
import time
import urllib.request

GEO_URL = "https://ipwho.is/"
WEATHER_URL = (
    "https://api.open-meteo.com/v1/forecast"
    "?latitude={lat}&longitude={lon}"
    "&current=temperature_2m,relative_humidity_2m,apparent_temperature,"
    "weather_code,wind_speed_10m,wind_direction_10m"
    "&daily=sunrise,sunset&timezone=auto&forecast_days=1"
)
BOOT_ID_FILE = "/proc/sys/kernel/random/boot_id"
CACHE_DIR = os.path.join(
    os.environ.get("XDG_CACHE_HOME", os.path.expanduser("~/.cache")),
    "waybar-weather",
)
CACHE_FILE = os.path.join(CACHE_DIR, "location.json")
GEO_TTL = 12 * 3600  # 12 hours

# Deployed config.env (see Makefile dotfiles target). Optional — absent on
# fresh machines until `make dotfiles` runs.
CONFIG_ENV = os.path.expanduser("~/.config/linux_setup/config.env")


def _load_config_env() -> None:
    """Load KEY=VALUE pairs from the deployed config.env into os.environ.

    Only sets vars that are not already in the environment (env vars win).
    No-op if the file doesn't exist.
    """
    if not os.path.isfile(CONFIG_ENV):
        return
    with open(CONFIG_ENV) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


_load_config_env()

# Fallback coords used only when geolocation fails and no cache exists.
# Values come from config.env (WEATHER_FALLBACK_*) with Tunis defaults.
FALLBACK_LAT = float(os.environ.get("WEATHER_FALLBACK_LAT", "36.81897"))
FALLBACK_LON = float(os.environ.get("WEATHER_FALLBACK_LON", "10.16579"))
FALLBACK_CITY = os.environ.get("WEATHER_FALLBACK_CITY", "Tunis")
FALLBACK_COUNTRY = os.environ.get("WEATHER_FALLBACK_COUNTRY", "TN")

# Static weather icon (Material Design Icons — weather-cloudy). Never changes.
WEATHER_ICON = "\U000F0590"


def _http_json(url: str, timeout: int = 10) -> dict | None:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "waybar-weather/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return None


def _boot_id() -> str:
    try:
        with open(BOOT_ID_FILE) as f:
            return f.read().strip()
    except OSError:
        return "unknown"


def _read_cache() -> dict | None:
    try:
        with open(CACHE_FILE) as f:
            data = json.load(f)
        if all(k in data for k in ("lat", "lon", "timestamp", "boot_id")):
            return data
    except (OSError, ValueError, json.JSONDecodeError):
        pass
    return None


def _write_cache(lat: float, lon: float, city: str, country: str, boot_id: str) -> None:
    os.makedirs(CACHE_DIR, exist_ok=True)
    data = {
        "lat": lat,
        "lon": lon,
        "city": city,
        "country": country,
        "boot_id": boot_id,
        "timestamp": int(time.time()),
    }
    tmp = CACHE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f)
    os.replace(tmp, CACHE_FILE)


def _geolocate() -> dict:
    """Return {'lat','lon','city','country','source'} for the location."""
    boot_id = _boot_id()
    cache = _read_cache()

    if cache is not None:
        fresh = cache.get("boot_id") == boot_id
        old = time.time() - cache["timestamp"] >= GEO_TTL
        if fresh and not old:
            return {"lat": cache["lat"], "lon": cache["lon"],
                    "city": cache["city"], "country": cache["country"],
                    "source": "cache"}

    geo = _http_json(GEO_URL)
    if geo is not None and geo.get("success"):
        lat, lon = float(geo["latitude"]), float(geo["longitude"])
        _write_cache(lat, lon, geo.get("city", ""), geo.get("country_code", ""), boot_id)
        return {"lat": lat, "lon": lon,
                "city": geo.get("city", ""), "country": geo.get("country_code", ""),
                "source": "ipwhois"}

    if cache is not None:
        return {"lat": cache["lat"], "lon": cache["lon"],
                "city": cache["city"], "country": cache["country"],
                "source": "stale-cache"}

    return {"lat": FALLBACK_LAT, "lon": FALLBACK_LON,
            "city": FALLBACK_CITY, "country": FALLBACK_COUNTRY,
            "source": "fallback"}


def _cardinal(deg: float) -> str:
    dirs = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
    return dirs[int((deg % 360) / 45) % 8]


def _fmt_time(iso: str) -> str:
    return iso[11:16] if len(iso) >= 16 else iso


def _describe(code: int) -> str:
    if code == 0:
        return "Clear sky"
    if code in (1, 2):
        return "Partly cloudy"
    if code == 3:
        return "Overcast"
    if code in (45, 48):
        return "Fog"
    if 51 <= code <= 67:
        return "Rain"
    if 71 <= code <= 86:
        return "Snow"
    if 95 <= code <= 99:
        return "Thunderstorm"
    return "Unknown"


def _build(data: dict, loc: dict) -> dict:
    current = data["current"]
    daily = data["daily"]
    temp = round(current["temperature_2m"])
    code = current["weather_code"]

    text = f"<span color='#D29922'>{WEATHER_ICON}</span>  {temp}\u00b0C"

    lines = []
    name = ", ".join(x for x in (loc["city"], loc["country"]) if x) or "Unknown"
    lines.append(f"{name} \u2014 {_describe(code)}")
    lines.append(f"Temperature: {temp}\u00b0C (feels like {round(current['apparent_temperature'])}\u00b0C)")
    lines.append(f"Humidity: {current['relative_humidity_2m']}%")
    lines.append(f"Wind: {round(current['wind_speed_10m'])} km/h {_cardinal(current['wind_direction_10m'])}")
    lines.append(f"Sunrise: {_fmt_time(daily['sunrise'][0])}  Sunset: {_fmt_time(daily['sunset'][0])}")

    return {"text": text, "tooltip": "\n".join(lines), "class": "normal"}


def main() -> None:
    loc = _geolocate()
    data = _http_json(WEATHER_URL.format(lat=loc["lat"], lon=loc["lon"]))

    if data is None or "current" not in data:
        print(json.dumps({"text": "<span color='#D29922'> </span>  --", "tooltip": "Weather unavailable",
                          "class": "error"}))
        return

    print(json.dumps(_build(data, loc)))


if __name__ == "__main__":
    main()
