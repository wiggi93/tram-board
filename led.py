"""Optional RGB LED matrix renderer.

Imported lazily by app.py so the rest of the project runs on any machine
without the rpi-rgb-led-matrix C library installed. Reads from BoardState
the same way the web preview does.
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime

from rgbmatrix import RGBMatrix, RGBMatrixOptions, graphics  # type: ignore

import tram

log = logging.getLogger(__name__)

FONT_PATH      = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts/4x6.bdf")
TEXT_FONT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts/7x14.bdf")
LED_COLS       = 64
LED_ROWS       = 32
CHAR_WIDTH     = 4
TEXT_CHAR_WIDTH = 7
TEXT_SCROLL_PX_PER_SEC = 30   # readable scroll speed for text mode

MAX_DEST_LEN = 9   # debug-frame default; actual visible chars derived from layout

SCROLL_PAUSE   = 3.5
SCROLL_SPEED   = 0.38
SCROLL_HOLD    = 2.0
SCROLL_STAGGER = 2.5


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


def _build_matrix() -> RGBMatrix:
    opts = RGBMatrixOptions()
    opts.rows          = int(os.environ.get("LED_ROWS", LED_ROWS))
    opts.cols          = int(os.environ.get("LED_COLS", LED_COLS))
    opts.chain_length  = int(os.environ.get("LED_CHAIN", "1"))
    opts.parallel      = int(os.environ.get("LED_PARALLEL", "1"))
    opts.gpio_slowdown = int(os.environ.get("LED_SLOWDOWN_GPIO", "2"))
    opts.pwm_bits      = int(os.environ.get("LED_PWM_BITS", "11"))
    opts.brightness    = int(os.environ.get("LED_BRIGHTNESS", "70"))
    mapping = os.environ.get("LED_GPIO_MAPPING")
    if mapping:
        opts.hardware_mapping = mapping            # e.g. "regular", "adafruit-hat", "adafruit-hat-pwm"
    if os.environ.get("LED_NO_HARDWARE_PULSE", "").lower() in ("1", "true", "yes", "on"):
        # Use software PWM. Avoids conflict with the Pi's snd_bcm2835 sound
        # module without requiring the user to disable audio on the host.
        # Trade-off: slight flicker compared to hardware PWM.
        opts.disable_hardware_pulsing = True
    return RGBMatrix(options=opts)


def _render_tram(canvas, font, board: tram.BoardState) -> None:
    dest_color  = graphics.Color(255, 127,  80)
    time_color  = graphics.Color(255, 255, 255)
    badge_bg    = graphics.Color(  0,  70, 200)
    badge_text  = graphics.Color(255, 255, 255)
    clock_color = graphics.Color(180, 180, 180)
    line_height = 6
    badge_width = 6
    dest_x      = badge_width + 1
    time_x      = LED_COLS - 2 * CHAR_WIDTH
    visible     = (time_x - dest_x) // CHAR_WIDTH - 1

    deps, changed_at = board.render_snapshot()
    for i, dep in enumerate(deps):
        if dep is None:
            continue
        y = line_height + i * line_height
        for fy in range(y - line_height + 1, y + 1):
            graphics.DrawLine(canvas, 0, fy, badge_width - 1, fy, badge_bg)
        graphics.DrawText(canvas, font, 1, y, badge_text, dep.line[:2])

        off  = _scroll_offset(i, len(dep.destination), visible)
        text = f" {dep.destination[off:off + visible]:<{visible}}"
        graphics.DrawText(canvas, font, dest_x, y, dest_color, text)

        elapsed = time.time() - changed_at[i]
        if elapsed < 1.5:
            b = int(min(1.0, elapsed / 1.5) * 255)
            row_color = graphics.Color(255, 255, b)
        else:
            row_color = time_color
        graphics.DrawText(canvas, font, time_x, y, row_color, f"{dep.minutes:>2}")

    now_str = datetime.now().strftime("%H:%M:%S")
    clock_x = (LED_COLS - len(now_str) * CHAR_WIDTH) // 2
    graphics.DrawText(canvas, font, clock_x, 31, clock_color, now_str)


def _render_text(canvas, font_big, text: str, text_t0: float) -> None:
    """Full-screen scrolling text. Text is repeated with a gap so it loops cleanly."""
    if not text:
        return
    color = graphics.Color(255, 200, 80)
    text_px   = len(text) * TEXT_CHAR_WIDTH
    cycle_px  = text_px + LED_COLS                      # text + a full screen of trailing gap
    elapsed   = time.time() - text_t0
    offset_px = int(elapsed * TEXT_SCROLL_PX_PER_SEC) % cycle_px
    x         = LED_COLS - offset_px
    # vertical centre for a 14px-tall font on a 32px panel: baseline ~22
    y         = 22
    graphics.DrawText(canvas, font_big, x, y, color, text)


def run(board: tram.BoardState) -> None:
    matrix    = _build_matrix()
    canvas    = matrix.CreateFrameCanvas()
    font      = graphics.Font(); font.LoadFont(FONT_PATH)
    font_big  = graphics.Font(); font_big.LoadFont(TEXT_FONT_PATH)

    while True:
        canvas.Clear()
        snap = board.snapshot()
        if snap.get("mode") == "text":
            _render_text(canvas, font_big, snap.get("text") or "", snap.get("text_changed_at") or 0.0)
        else:
            _render_tram(canvas, font, board)

        time.sleep(0.1)
        canvas = matrix.SwapOnVSync(canvas)
