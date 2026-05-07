"""EFA transit API client and shared board state.

Owns the data model (`Departure`), the thread-safe `BoardState`, and the
background fetch loop. Has no UI dependencies — both the LED renderer and
the Flask web app consume `BoardState` snapshots.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Optional

import requests

log = logging.getLogger(__name__)

# ── EFA endpoints ─────────────────────────────────────────────────────────────

EFA_BASE         = "https://efa.vrr.de/standard"   # broader coverage than efa.de (includes NRW)
API_URL          = f"{EFA_BASE}/XML_DM_REQUEST"
STOPFINDER_URL   = f"{EFA_BASE}/XML_STOPFINDER_REQUEST"
API_VERSION      = "10.6.14.22"
TRAM_CLASSES     = {3, 4}             # 3 = Stadtbahn (light rail), 4 = Straßenbahn

DISPLAY_ROWS     = 4
FETCH_INTERVAL   = 30                 # seconds between API polls
REQUEST_TIMEOUT  = 10                 # seconds before a request is abandoned


# ── Data model ────────────────────────────────────────────────────────────────

@dataclass
class Departure:
    line: str
    destination: str
    minutes: int


# ── Shared board state ────────────────────────────────────────────────────────

class BoardState:
    """Thread-safe container for the current departure list and station info."""

    def __init__(self) -> None:
        self._lock        = threading.Lock()
        self._departures: list[Optional[Departure]] = [None] * DISPLAY_ROWS
        self._changed_at: list[float] = [0.0] * DISPLAY_ROWS
        self._station_name: str = ""
        self._station_city: str = ""
        self._last_update: float = 0.0
        self._last_error:  Optional[str] = None
        self._mode:        str = "tram"
        self._text:        str = ""
        self._text_changed_at: float = 0.0
        self._weather_periods: list[dict] = []
        self._weather_place: str = ""
        self._weather_updated_at: float = 0.0
        self._weather_error: Optional[str] = None

    def update(self, new: list[Departure]) -> None:
        with self._lock:
            for i in range(DISPLAY_ROWS):
                dep = new[i] if i < len(new) else None
                old = self._departures[i]
                if old is None or dep is None or old.minutes != dep.minutes:
                    self._changed_at[i] = time.time()
                self._departures[i] = dep
            self._last_update = time.time()
            self._last_error  = None

    def set_station(self, name: str, city: str) -> None:
        with self._lock:
            self._station_name = name
            self._station_city = city

    def set_error(self, msg: str) -> None:
        with self._lock:
            self._last_error = msg

    def set_mode(self, mode: str) -> None:
        with self._lock:
            self._mode = mode

    def set_text(self, text: str) -> None:
        with self._lock:
            if text != self._text:
                self._text_changed_at = time.time()
            self._text = text

    def set_weather(self, periods: list[dict], place: str) -> None:
        with self._lock:
            self._weather_periods = list(periods)
            self._weather_place = place
            self._weather_updated_at = time.time()
            self._weather_error = None

    def set_weather_error(self, msg: str) -> None:
        with self._lock:
            self._weather_error = msg

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "departures":   [d.__dict__ if d else None for d in self._departures],
                "changed_at":   list(self._changed_at),
                "station_name": self._station_name,
                "station_city": self._station_city,
                "last_update":  self._last_update,
                "last_error":   self._last_error,
                "mode":         self._mode,
                "text":         self._text,
                "text_changed_at": self._text_changed_at,
                "weather": {
                    "place":      self._weather_place,
                    "periods":    list(self._weather_periods),
                    "updated_at": self._weather_updated_at,
                    "error":      self._weather_error,
                },
            }

    def render_snapshot(self) -> tuple[list[Optional[Departure]], list[float]]:
        """Lighter snapshot for renderers that just need the departure rows."""
        with self._lock:
            return list(self._departures), list(self._changed_at)

    def get_station_city(self) -> str:
        with self._lock:
            return self._station_city


# ── EFA API ───────────────────────────────────────────────────────────────────

@dataclass
class ResolvedStop:
    id: str
    name: str
    city: str


def find_stop(query: str) -> ResolvedStop:
    """Resolve a free-text query (e.g. 'Kerstingstraße Hannover') to a stop."""
    log.info("Resolving stop for query: %r", query)
    params = {
        "outputFormat": "rapidJSON",
        "type_sf":      "any",
        "name_sf":      query,
        "version":      API_VERSION,
    }
    response = requests.get(STOPFINDER_URL, params=params, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    locations = response.json().get("locations") or []
    stops = [loc for loc in locations if loc.get("type") == "stop" and loc.get("id")]
    if not stops:
        raise RuntimeError(f"No stops found for query: {query!r}")
    best = stops[0]
    name = best.get("disassembledName") or best.get("name") or ""
    city = (best.get("parent") or {}).get("name") or ""
    log.info("Resolved %r → %s (%s, %s)", query, best["id"], name, city)
    return ResolvedStop(id=best["id"], name=name, city=city)


def fetch_stop_events(stop_id: str) -> list[dict]:
    params = {
        "outputFormat": "rapidJSON",
        "type_dm":      "any",
        "name_dm":      stop_id,
        "mode":         "direct",
        "useRealtime":  "1",
        "depType":      "stopEvents",
        "limit":        "14",
        "version":      API_VERSION,
    }
    response = requests.get(API_URL, params=params, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    return response.json().get("stopEvents", [])


def parse_departures(events: list[dict]) -> list[Departure]:
    now = datetime.now(timezone.utc)
    result: list[Departure] = []
    for event in events:
        transport = event.get("transportation", {})
        if transport.get("product", {}).get("class") not in TRAM_CLASSES:
            continue
        line        = transport.get("number", "?")
        raw_dest    = transport.get("destination", {}).get("name", "?")
        destination = re.sub(r"\s*\(.*?\)", "", raw_dest).rstrip("/ ")
        dep_str     = event.get("departureTimeEstimated") or event.get("departureTimePlanned")
        if not dep_str:
            continue
        dt   = datetime.fromisoformat(dep_str.replace("Z", "+00:00"))
        secs = (dt - now).total_seconds()
        if secs < -60:                     # already departed more than a minute ago
            continue
        minutes = max(0, int(secs / 60))
        result.append(Departure(line=line, destination=destination, minutes=minutes))
    return sorted(result, key=lambda d: d.minutes)


# ── Display polish ────────────────────────────────────────────────────────────

BUSY_WINDOW_MIN = 5    # if DISPLAY_ROWS+ trams arrive within this window, the stop is "busy"


def trim_for_display(departures: list[Departure], city: str = "") -> list[Departure]:
    """At busy hubs (e.g. Köln Neumarkt) the next several departures cluster in
    the same minute and dominate the board. When that happens, strip the
    redundant parent-city prefix from destinations and dedupe by
    (line, destination) so each row shows a distinct route. Quiet stops are
    returned unchanged.
    """
    is_busy = (
        len(departures) >= DISPLAY_ROWS
        and departures[DISPLAY_ROWS - 1].minutes <= BUSY_WINDOW_MIN
    )
    if not is_busy:
        return departures

    prefix = f"{city} " if city else ""
    seen:  set[tuple[str, str]] = set()
    out:   list[Departure] = []
    for d in departures:
        dest = d.destination[len(prefix):] if prefix and d.destination.startswith(prefix) else d.destination
        key  = (d.line, dest)
        if key in seen:
            continue
        seen.add(key)
        out.append(Departure(line=d.line, destination=dest, minutes=d.minutes))
    return out


# ── Background fetch loop ─────────────────────────────────────────────────────

def fetch_loop(
    board:        BoardState,
    get_stop_id:  Callable[[], Optional[str]],
    stop_event:   Optional[threading.Event] = None,
) -> None:
    """Fetch departures every FETCH_INTERVAL seconds.

    `get_stop_id` is called every iteration so the loop picks up station
    changes made through the web UI without restart.
    """
    while not (stop_event and stop_event.is_set()):
        stop_id = get_stop_id()
        if not stop_id:
            log.info("No stop configured yet; waiting.")
        else:
            try:
                events     = fetch_stop_events(stop_id)
                departures = parse_departures(events)
                departures = trim_for_display(departures, city=board.get_station_city())
                board.update(departures)
                for dep in departures[:DISPLAY_ROWS]:
                    log.info("→ %s to %s in %d min", dep.line, dep.destination, dep.minutes)
            except Exception as e:
                log.error("Fetch error: %s", e)
                board.set_error(str(e))
        if stop_event:
            stop_event.wait(FETCH_INTERVAL)
        else:
            time.sleep(FETCH_INTERVAL)
