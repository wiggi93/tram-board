"""Flask web service for the tram departure board.

Routes:
    GET  /                    Admin page with live preview
    GET  /api/state           JSON snapshot of current departures and station
    POST /api/station         {"query": "..."} → resolve and switch station
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import threading

from flask import Flask, jsonify, render_template, request

import config
import tram
import weather

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("tram-board")

app   = Flask(__name__)
board = tram.BoardState()
stop_event = threading.Event()


def _current_stop_id() -> str:
    return config.load().stop_id


def _current_weather_query() -> str:
    cfg = config.load()
    return cfg.station_city or cfg.station_name or cfg.station_query


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/state")
def api_state():
    cfg  = config.load()
    snap = board.snapshot()
    snap["station_query"] = cfg.station_query
    snap["station_name"]  = cfg.station_name or snap["station_name"]
    snap["station_city"]  = cfg.station_city or snap["station_city"]
    snap["stop_id"]       = cfg.stop_id
    return jsonify(snap)


@app.get("/api/weather")
def api_weather():
    cfg = config.load()
    # Open-Meteo's geocoder only knows cities/towns, not tram stops — so prefer
    # the resolved city over the full station name.
    query = cfg.station_city or cfg.station_name or cfg.station_query
    summary = weather.fetch_summary(query)
    return jsonify(summary)


@app.post("/api/mode")
def api_set_mode():
    payload = request.get_json(silent=True) or {}
    mode = (payload.get("mode") or "").strip()
    if mode not in ("tram", "text", "weather", "rotate"):
        return jsonify(error="mode must be 'tram', 'text', 'weather' or 'rotate'"), 400
    cfg = config.load()
    cfg.mode = mode
    config.save(cfg)
    board.set_mode(mode)
    return jsonify(mode=mode)


@app.post("/api/rotate")
def api_set_rotate_interval():
    payload = request.get_json(silent=True) or {}
    try:
        interval = int(payload.get("interval"))
    except (TypeError, ValueError):
        return jsonify(error="interval must be an integer (seconds)"), 400
    if interval < 3 or interval > 600:
        return jsonify(error="interval must be between 3 and 600 seconds"), 400
    cfg = config.load()
    cfg.rotate_interval = interval
    config.save(cfg)
    board.set_rotate_interval(interval)
    return jsonify(interval=interval)


@app.post("/api/text")
def api_set_text():
    payload = request.get_json(silent=True) or {}
    text = payload.get("text") or ""
    cfg = config.load()
    cfg.text = text
    config.save(cfg)
    board.set_text(text)
    return jsonify(text=text)


@app.post("/api/station")
def api_set_station():
    payload = request.get_json(silent=True) or {}
    query = (payload.get("query") or "").strip()
    if not query:
        return jsonify(error="query is required"), 400
    try:
        resolved = tram.find_stop(query)
    except Exception as e:
        log.error("Resolve failed for %r: %s", query, e)
        return jsonify(error=f"could not resolve station: {e}"), 502

    cfg = config.Config(
        station_query=query,
        stop_id=resolved.id,
        station_name=resolved.name,
        station_city=resolved.city,
    )
    config.save(cfg)
    board.set_station(resolved.name, resolved.city)
    # Pull a fresh schedule synchronously so the panel reflects the new station
    # immediately instead of waiting up to FETCH_INTERVAL for the next tick.
    try:
        events     = tram.fetch_stop_events(resolved.id)
        departures = tram.parse_departures(events)
        departures = tram.trim_for_display(departures, city=resolved.city)
        board.update(departures)
    except Exception as e:
        log.warning("Initial fetch after station change failed: %s", e)
        board.update([])
    return jsonify(
        stop_id=resolved.id,
        station_name=resolved.name,
        station_city=resolved.city,
    )


def _start_background_threads(enable_led: bool) -> None:
    cfg = config.load()
    if cfg.station_name:
        board.set_station(cfg.station_name, cfg.station_city)
    board.set_mode(cfg.mode or "tram")
    board.set_text(cfg.text or "")
    if cfg.rotate_interval:
        board.set_rotate_interval(cfg.rotate_interval)

    fetch_thread = threading.Thread(
        target=tram.fetch_loop,
        args=(board, _current_stop_id, stop_event),
        name="fetch",
        daemon=True,
    )
    fetch_thread.start()

    weather_thread = threading.Thread(
        target=weather.fetch_loop,
        args=(board, _current_weather_query, stop_event),
        name="weather",
        daemon=True,
    )
    weather_thread.start()

    if enable_led:
        try:
            import led  # lazy: only import when requested
            led_thread = threading.Thread(target=led.run, args=(board,), name="led", daemon=True)
            led_thread.start()
            log.info("LED renderer started.")
        except Exception as e:
            log.error("Could not start LED renderer: %s", e)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default=os.environ.get("HOST", "0.0.0.0"))
    parser.add_argument("--port", default=int(os.environ.get("PORT", "8080")), type=int)
    parser.add_argument("--led", action="store_true",
                        help="Enable LED matrix output (Raspberry Pi only).")
    args = parser.parse_args()

    _start_background_threads(enable_led=args.led)
    log.info("Listening on http://%s:%d", args.host, args.port)
    try:
        app.run(host=args.host, port=args.port, threaded=True, use_reloader=False)
    finally:
        stop_event.set()


if __name__ == "__main__":
    main()
