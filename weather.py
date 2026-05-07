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
from typing import Callable, Optional

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


def _geocode(query: str) -> tuple[Optional[tuple[float, float, str]], Optional[str]]:
    """Resolve a place name to (lat, lon, display_name).

    Returns (loc, error). On success error is None; on failure loc is None and
    error is a short human-readable reason — split out so callers can surface
    useful diagnostics instead of a generic "could not locate".
    """
    if not query:
        return None, "empty query"
    with _lock:
        cached = _geo_cache.get(query)
    if cached:
        return cached, None
    try:
        r = requests.get(
            GEOCODE_URL,
            params={"name": query, "count": 1, "language": "en", "format": "json"},
            timeout=REQUEST_TIMEOUT,
        )
    except requests.Timeout:
        log.warning("Geocoding timed out for %r", query)
        return None, "geocoding timeout"
    except requests.RequestException as e:
        log.warning("Geocoding network error for %r: %s", query, e)
        return None, f"geocoding network error: {type(e).__name__}"
    if r.status_code != 200:
        log.warning("Geocoding HTTP %s for %r: %s", r.status_code, query, r.text[:200])
        return None, f"geocoding HTTP {r.status_code}"
    try:
        body = r.json()
    except ValueError:
        log.warning("Geocoding returned non-JSON for %r: %s", query, r.text[:200])
        return None, "geocoding returned non-JSON"
    results = body.get("results") or []
    if not results:
        log.info("Geocoder returned no results for %r (raw=%s)", query, str(body)[:200])
        return None, f"could not locate {query!r}"
    top = results[0]
    loc = (float(top["latitude"]), float(top["longitude"]), top.get("name") or query)
    with _lock:
        _geo_cache[query] = loc
    return loc, None


def _classify(code: int) -> str:
    """Map an Open-Meteo WMO code to a small set of icon categories."""
    if code == 0:                            return "sun"
    if code in (1, 2):                       return "cloudy_sun"
    if code == 3:                            return "cloud"
    if code in (45, 48):                     return "fog"
    if code in (51, 53, 55, 56, 57,
                61, 63, 65, 66, 67,
                80, 81, 82):                 return "rain"
    if code in (71, 73, 75, 77, 85, 86):     return "snow"
    if code in (95, 96, 99):                 return "thunder"
    return "cloud"


def _summarise(period_label: str, hours: list[dict]) -> dict:
    if not hours:
        return {
            "label":   period_label,
            "icon":    "cloud",
            "temp_lo": None,
            "temp_hi": None,
            "precip":  0,
            "summary": "no data",
        }
    temps  = [h["temperature"] for h in hours if h["temperature"] is not None]
    codes  = [h["code"]        for h in hours if h["code"]        is not None]
    precs  = [h["precip"]      for h in hours if h["precip"]      is not None]
    dominant = Counter(codes).most_common(1)[0][0] if codes else None
    icon     = _classify(dominant) if dominant is not None else "cloud"
    lo = round(min(temps)) if temps else None
    hi = round(max(temps)) if temps else None
    precip = int(round(max(precs))) if precs else 0

    parts: list[str] = []
    if dominant is not None:
        parts.append(WEATHER_CODE.get(dominant, f"code {dominant}"))
    if lo is not None and hi is not None:
        parts.append(f"{lo}°C" if lo == hi else f"{lo}–{hi}°C")
    if precip >= 20:
        parts.append(f"rain {precip}%")
    return {
        "label":   period_label,
        "icon":    icon,
        "temp_lo": lo,
        "temp_hi": hi,
        "precip":  precip,
        "summary": ", ".join(parts) or "—",
    }


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

    geo, geo_err = _geocode(query)
    if not geo:
        return {"error": geo_err or f"could not locate {query!r}"}
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


# ── Background refresh ────────────────────────────────────────────────────────

FETCH_INTERVAL       = 10 * 60   # seconds — between successful refreshes
FETCH_RETRY_INTERVAL = 60        # seconds — between retries when last fetch failed


def fetch_loop(
    board,
    get_query: Callable[[], Optional[str]],
    stop_event: Optional[threading.Event] = None,
) -> None:
    """Periodically refresh the weather forecast on the BoardState.

    Mirrors tram.fetch_loop: pulls the current query each iteration so a
    station change in the admin UI is picked up without a restart.
    """
    last_query: Optional[str] = None
    last_failed = False
    while not (stop_event and stop_event.is_set()):
        query = get_query() or ""
        if query:
            data = fetch_summary(query)
            if "error" in data:
                board.set_weather_error(data["error"])
                last_failed = True
            else:
                board.set_weather(data.get("periods") or [], data.get("place") or "")
                last_failed = False
            last_query = query
        # Wait, but wake up early if the station changes so the panel updates
        # quickly after the user picks a new station. Retry sooner after a
        # failure so transient API blips clear up without a 10-min stall.
        wait_left = FETCH_RETRY_INTERVAL if last_failed else FETCH_INTERVAL
        while wait_left > 0 and not (stop_event and stop_event.is_set()):
            slice_s = min(2.0, wait_left)
            if stop_event:
                stop_event.wait(slice_s)
            else:
                time.sleep(slice_s)
            wait_left -= slice_s
            current = get_query() or ""
            if current and current != last_query:
                break  # forces an immediate refresh on station change
