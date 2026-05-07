"""Open-Meteo weather lookup for the configured station's city.

Provides a same-day, period-by-period summary (morning/lunchtime/afternoon/
evening/night) for display in the admin UI's Weather tab. Uses Open-Meteo's
free geocoding + forecast APIs (no key required) and caches results in-process.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import Counter
from datetime import datetime, timedelta
from typing import Optional

import requests

log = logging.getLogger(__name__)

GEOCODE_URL  = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
REQUEST_TIMEOUT = 8

# Refresh at most every 15 minutes per (city, day) key.
CACHE_TTL = 15 * 60

# Hour buckets, half-open ranges [start, end).
PERIODS = [
    ("Morning",   6,  11),
    ("Lunchtime", 11, 14),
    ("Afternoon", 14, 18),
    ("Evening",   18, 22),
    ("Night",     22, 30),  # 30 wraps to next day's 06:00
]

# Subset of WMO weather codes returned by Open-Meteo, mapped to short labels.
WEATHER_CODE = {
    0: "clear",
    1: "mostly clear",
    2: "partly cloudy",
    3: "overcast",
    45: "foggy",
    48: "icy fog",
    51: "light drizzle",
    53: "drizzle",
    55: "heavy drizzle",
    56: "freezing drizzle",
    57: "freezing drizzle",
    61: "light rain",
    63: "rain",
    65: "heavy rain",
    66: "freezing rain",
    67: "freezing rain",
    71: "light snow",
    73: "snow",
    75: "heavy snow",
    77: "snow grains",
    80: "light showers",
    81: "showers",
    82: "violent showers",
    85: "snow showers",
    86: "heavy snow showers",
    95: "thunderstorm",
    96: "thunder + hail",
    99: "severe thunder + hail",
}


_lock = threading.Lock()
_geo_cache: dict[str, tuple[float, float, str]] = {}
_forecast_cache: dict[str, tuple[float, dict]] = {}


def _geocode(query: str) -> Optional[tuple[float, float, str]]:
    if not query:
        return None
    with _lock:
        cached = _geo_cache.get(query)
    if cached:
        return cached
    try:
        r = requests.get(
            GEOCODE_URL,
            params={"name": query, "count": 1, "language": "en", "format": "json"},
            timeout=REQUEST_TIMEOUT,
        )
        r.raise_for_status()
        results = r.json().get("results") or []
        if not results:
            return None
        top = results[0]
        loc = (float(top["latitude"]), float(top["longitude"]), top.get("name") or query)
        with _lock:
            _geo_cache[query] = loc
        return loc
    except Exception as e:
        log.warning("Geocoding failed for %r: %s", query, e)
        return None


def _summarise(period_label: str, hours: list[dict]) -> dict:
    if not hours:
        return {"label": period_label, "summary": "no data"}
    temps  = [h["temperature"] for h in hours if h["temperature"] is not None]
    codes  = [h["code"]        for h in hours if h["code"]        is not None]
    precs  = [h["precip"]      for h in hours if h["precip"]      is not None]
    parts: list[str] = []
    if codes:
        dominant = Counter(codes).most_common(1)[0][0]
        parts.append(WEATHER_CODE.get(dominant, f"code {dominant}"))
    if temps:
        lo, hi = min(temps), max(temps)
        parts.append(f"{round(lo)}–{round(hi)}°C" if round(lo) != round(hi) else f"{round(lo)}°C")
    if precs and max(precs) >= 20:
        parts.append(f"rain {int(max(precs))}%")
    return {"label": period_label, "summary": ", ".join(parts) or "—"}


def _bucket_hours(hourly: dict, today: str, tomorrow: str) -> list[dict]:
    """Combine today + tomorrow hourly arrays into a flat list with absolute hour offsets.

    Hour offset: today 0..23, tomorrow 24..47. Lets us straddle midnight for "Night".
    """
    times  = hourly.get("time")          or []
    temps  = hourly.get("temperature_2m") or []
    codes  = hourly.get("weathercode")    or hourly.get("weather_code") or []
    precs  = hourly.get("precipitation_probability") or []
    out: list[dict] = []
    for i, t in enumerate(times):
        date_part, _, hour_part = t.partition("T")
        try:
            hour = int(hour_part[:2])
        except ValueError:
            continue
        if date_part == today:
            offset = hour
        elif date_part == tomorrow:
            offset = hour + 24
        else:
            continue
        out.append({
            "offset":      offset,
            "temperature": temps[i] if i < len(temps) else None,
            "code":        codes[i] if i < len(codes) else None,
            "precip":      precs[i] if i < len(precs) else None,
        })
    return out


def fetch_summary(query: str) -> dict:
    """Return a same-day weather summary dict, or an error dict."""
    if not query:
        return {"error": "no station configured"}

    today_str = datetime.now().strftime("%Y-%m-%d")
    cache_key = f"{query}|{today_str}"
    now = time.time()
    with _lock:
        cached = _forecast_cache.get(cache_key)
    if cached and now - cached[0] < CACHE_TTL:
        return cached[1]

    geo = _geocode(query)
    if not geo:
        return {"error": f"could not locate {query!r}"}
    lat, lon, place = geo

    try:
        r = requests.get(
            FORECAST_URL,
            params={
                "latitude":  lat,
                "longitude": lon,
                "hourly":    "temperature_2m,weathercode,precipitation_probability",
                "timezone":  "auto",
                "forecast_days": 2,
            },
            timeout=REQUEST_TIMEOUT,
        )
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        log.warning("Forecast fetch failed for %s: %s", query, e)
        return {"error": f"weather fetch failed: {e}"}

    tz_now = datetime.now()  # Open-Meteo with timezone=auto returns local times
    today    = tz_now.strftime("%Y-%m-%d")
    tomorrow = (tz_now + timedelta(days=1)).strftime("%Y-%m-%d")

    hours = _bucket_hours(data.get("hourly") or {}, today, tomorrow)

    periods = []
    for label, start, end in PERIODS:
        bucket = [h for h in hours if start <= h["offset"] < end]
        periods.append(_summarise(label, bucket))

    summary = {
        "place":   place,
        "date":    today,
        "periods": periods,
    }
    with _lock:
        _forecast_cache[cache_key] = (now, summary)
    return summary
