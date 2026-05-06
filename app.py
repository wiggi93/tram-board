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

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("tram-board")

app   = Flask(__name__)
board = tram.BoardState()
stop_event = threading.Event()


def _current_stop_id() -> str:
    return config.load().stop_id


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
    # Clear current departures so the UI shows the change immediately.
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

    fetch_thread = threading.Thread(
        target=tram.fetch_loop,
        args=(board, _current_stop_id, stop_event),
        name="fetch",
        daemon=True,
    )
    fetch_thread.start()

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
