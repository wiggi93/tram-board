#!/usr/bin/env python3
"""Host-side LED driver for tram-board.

Runs OUTSIDE Docker so the rpi-rgb-led-matrix C library can get the
real-time scheduling it needs (Docker on Raspberry Pi OS has no
CONFIG_RT_GROUP_SCHED, so cgroup-bound processes can't reliably hold
SCHED_FIFO and the panel renders garbage).

Polls the Dockerized web service's /api/state and renders the panel
identically to how the in-container LED renderer would. The container
keeps owning the EFA fetching, web admin, config persistence, and
JSON API; this script is just a dumb pixel pusher.

Usage:
    sudo python3 led-driver.py
    # or via the systemd unit installed by ./install.sh

Environment variables (mirror the in-container LED renderer):
    TRAM_BOARD_URL         (default http://127.0.0.1:8080)
    LED_ROWS               (default 32)
    LED_COLS               (default 64)
    LED_CHAIN              (default 1)
    LED_PARALLEL           (default 1)
    LED_BRIGHTNESS         (default 70)
    LED_PWM_BITS           (default 11)
    LED_SLOWDOWN_GPIO      (default 2)
    LED_GPIO_MAPPING       (e.g. "adafruit-hat"; unset uses regular wiring)
    LED_NO_HARDWARE_PULSE  ("1" forces software PWM if you can't disable
                            the Pi's snd_bcm2835 module)
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime

import requests
from rgbmatrix import RGBMatrix, RGBMatrixOptions, graphics  # type: ignore

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("tram-board-led")

# ── Config ────────────────────────────────────────────────────────────────────

API_URL        = os.environ.get("TRAM_BOARD_URL", "http://127.0.0.1:8080").rstrip("/") + "/api/state"
POLL_INTERVAL  = 0.25      # seconds — matches the browser's preview cadence

LED_ROWS       = int(os.environ.get("LED_ROWS", "32"))
LED_COLS       = int(os.environ.get("LED_COLS", "64"))
CHAR_WIDTH     = 4
TEXT_CHAR_W    = 7
TEXT_SCROLL_PX = 30        # pixels per second

DISPLAY_ROWS   = 4
SCROLL_PAUSE   = 3.5
SCROLL_SPEED   = 0.38
SCROLL_HOLD    = 2.0
SCROLL_STAGGER = 2.5

SCRIPT_DIR     = os.path.dirname(os.path.abspath(__file__))
FONT_PATH      = os.environ.get("LED_FONT_TRAM", os.path.join(SCRIPT_DIR, "fonts/4x6.bdf"))
TEXT_FONT_PATH = os.environ.get("LED_FONT_TEXT", os.path.join(SCRIPT_DIR, "fonts/7x14.bdf"))


# ── Matrix init ───────────────────────────────────────────────────────────────

def _build_matrix() -> RGBMatrix:
    opts = RGBMatrixOptions()
    opts.rows          = LED_ROWS
    opts.cols          = LED_COLS
    opts.chain_length  = int(os.environ.get("LED_CHAIN", "1"))
    opts.parallel      = int(os.environ.get("LED_PARALLEL", "1"))
    opts.gpio_slowdown = int(os.environ.get("LED_SLOWDOWN_GPIO", "2"))
    opts.pwm_bits      = int(os.environ.get("LED_PWM_BITS", "11"))
    opts.brightness    = int(os.environ.get("LED_BRIGHTNESS", "70"))
    mapping = os.environ.get("LED_GPIO_MAPPING")
    if mapping:
        opts.hardware_mapping = mapping
    if os.environ.get("LED_NO_HARDWARE_PULSE", "").lower() in ("1", "true", "yes", "on"):
        opts.disable_hardware_pulsing = True
    return RGBMatrix(options=opts)


# ── Scroll math ───────────────────────────────────────────────────────────────

def _scroll_offset(row: int, dest_len: int, visible: int) -> int:
    max_off = max(0, dest_len - visible)
    if max_off == 0:
        return 0
    scroll_dur = max_off * SCROLL_SPEED
    cycle      = SCROLL_PAUSE + scroll_dur + SCROLL_HOLD
    t          = (time.time() - row * SCROLL_STAGGER) % cycle
    if t < SCROLL_PAUSE:
        return 0
    if t < SCROLL_PAUSE + scroll_dur:
        return int((t - SCROLL_PAUSE) / SCROLL_SPEED)
    return max_off


# ── Renderers ─────────────────────────────────────────────────────────────────

def _render_tram(canvas, font, snap: dict) -> None:
    dest_color  = graphics.Color(255, 127,  80)
    time_color  = graphics.Color(255, 255, 255)
    badge_bg    = graphics.Color(  0,  70, 200)
    badge_text  = graphics.Color(255, 255, 255)
    clock_color = graphics.Color(180, 180, 180)
    line_height = 6
    # Badge fits up to two characters (4px each) with 1px margin on either side.
    badge_width = 2 * CHAR_WIDTH + 2
    dest_x      = badge_width + 1
    time_x      = LED_COLS - 2 * CHAR_WIDTH
    visible     = (time_x - dest_x) // CHAR_WIDTH - 1

    deps       = snap.get("departures") or []
    changed_at = snap.get("changed_at") or [0.0] * len(deps)

    for i, dep in enumerate(deps[:DISPLAY_ROWS]):
        if not dep:
            continue
        y = line_height + i * line_height
        for fy in range(y - line_height + 1, y + 1):
            graphics.DrawLine(canvas, 0, fy, badge_width - 1, fy, badge_bg)
        # Centre the line number inside the badge so single- and double-digit
        # numbers both look balanced.
        line_str = dep["line"][:2]
        line_x   = max(1, (badge_width - len(line_str) * CHAR_WIDTH) // 2)
        graphics.DrawText(canvas, font, line_x, y, badge_text, line_str)

        off  = _scroll_offset(i, len(dep["destination"]), visible)
        text = f" {dep['destination'][off:off + visible]:<{visible}}"
        graphics.DrawText(canvas, font, dest_x, y, dest_color, text)

        elapsed = time.time() - (changed_at[i] if i < len(changed_at) else 0)
        if elapsed < 1.5:
            b = int(min(1.0, elapsed / 1.5) * 255)
            row_color = graphics.Color(255, 255, b)
        else:
            row_color = time_color
        graphics.DrawText(canvas, font, time_x, y, row_color, f"{dep['minutes']:>2}")

    now_str = datetime.now().strftime("%H:%M:%S")
    clock_x = (LED_COLS - len(now_str) * CHAR_WIDTH) // 2
    graphics.DrawText(canvas, font, clock_x, 31, clock_color, now_str)


def _render_text(canvas, font_big, snap: dict) -> None:
    text = snap.get("text") or ""
    if not text:
        return
    t0        = snap.get("text_changed_at") or 0.0
    color     = graphics.Color(255, 200, 80)
    text_px   = len(text) * TEXT_CHAR_W
    cycle_px  = text_px + LED_COLS
    elapsed   = time.time() - t0
    offset_px = int(elapsed * TEXT_SCROLL_PX) % cycle_px
    x         = LED_COLS - offset_px
    graphics.DrawText(canvas, font_big, x, 22, color, text)


# ── Main loop ─────────────────────────────────────────────────────────────────

def main() -> None:
    log.info("Starting; polling %s every %.0f ms", API_URL, POLL_INTERVAL * 1000)
    matrix    = _build_matrix()
    canvas    = matrix.CreateFrameCanvas()
    font      = graphics.Font(); font.LoadFont(FONT_PATH)
    font_big  = graphics.Font(); font_big.LoadFont(TEXT_FONT_PATH)

    snap: dict = {"mode": "tram", "departures": [None] * DISPLAY_ROWS, "changed_at": [0.0] * DISPLAY_ROWS}
    last_fetch = 0.0

    while True:
        now = time.time()
        if now - last_fetch >= POLL_INTERVAL:
            try:
                snap = requests.get(API_URL, timeout=2).json()
            except Exception as e:
                log.warning("Poll failed (%s) — keeping last snapshot", e)
            last_fetch = now

        canvas.Clear()
        if snap.get("mode") == "text":
            _render_text(canvas, font_big, snap)
        else:
            _render_tram(canvas, font, snap)
        time.sleep(0.05)
        canvas = matrix.SwapOnVSync(canvas)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log.info("Exiting.")
