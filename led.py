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
    # Badge fits up to two characters (4px each) with 1px margin on either side.
    badge_width = 2 * CHAR_WIDTH + 2
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
        # Centre the line number inside the badge so single- and double-digit
        # numbers both look balanced.
        line_str = dep.line[:2]
        line_x   = max(1, (badge_width - len(line_str) * CHAR_WIDTH) // 2)
        graphics.DrawText(canvas, font, line_x, y, badge_text, line_str)

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


# ── Weather ───────────────────────────────────────────────────────────────────
#
# 6×6 sprite per icon category. Characters in the rows below map to RGB tuples
# in the icon's palette. '.' = unlit. Sprites occupy the leftmost 6 px of each
# 6-px row, so they line up vertically with the 4×6 text font's baseline.

WEATHER_ICONS: dict[str, tuple[list[str], dict[str, tuple[int, int, int]]]] = {
    "sun": (
        [
            "..XX..",
            "X....X",
            ".XXXX.",
            ".XXXX.",
            "X....X",
            "..XX..",
        ],
        {"X": (255, 220, 60)},
    ),
    "cloudy_sun": (
        [
            "..X...",
            "XXX...",
            ".X.YY.",
            "YYYYYY",
            ".YYYY.",
            "......",
        ],
        {"X": (255, 220, 60), "Y": (180, 180, 180)},
    ),
    "cloud": (
        [
            "..XX..",
            ".XXXX.",
            "XXXXXX",
            ".XXXX.",
            "......",
            "......",
        ],
        {"X": (180, 180, 180)},
    ),
    "rain": (
        [
            ".XXXX.",
            "XXXXXX",
            ".XXXX.",
            "Y.Y.Y.",
            ".Y.Y.Y",
            "......",
        ],
        {"X": (180, 180, 180), "Y": (100, 170, 255)},
    ),
    "snow": (
        [
            ".XXXX.",
            "XXXXXX",
            "......",
            "Y.Y.Y.",
            ".Y.Y.Y",
            "Y.Y.Y.",
        ],
        {"X": (180, 180, 180), "Y": (255, 255, 255)},
    ),
    "thunder": (
        [
            ".XXXX.",
            "XXXXXX",
            "..Y...",
            ".YY...",
            "..Y...",
            ".Y....",
        ],
        {"X": (180, 180, 180), "Y": (255, 220, 60)},
    ),
    "fog": (
        [
            "XXXXXX",
            "......",
            "XXXXXX",
            "......",
            "XXXXXX",
            "......",
        ],
        {"X": (180, 180, 180)},
    ),
}


def _draw_weather_icon(canvas, name: str, x0: int, y0: int) -> None:
    rows, palette = WEATHER_ICONS.get(name) or WEATHER_ICONS["cloud"]
    for dy, row in enumerate(rows):
        for dx, ch in enumerate(row):
            rgb = palette.get(ch)
            if rgb:
                canvas.SetPixel(x0 + dx, y0 + dy, *rgb)


def _render_weather(canvas, font, snap: dict) -> None:
    weather = snap.get("weather") or {}
    periods = weather.get("periods") or []
    err     = weather.get("error")
    text_color  = graphics.Color(255, 255, 255)
    time_color  = graphics.Color(255, 200, 80)
    humid_color = graphics.Color(140, 195, 255)

    if not periods:
        msg = (err or "loading weather…")[:15]
        graphics.DrawText(canvas, font, 1, 18, text_color, msg)
        return

    # Column layout (px) within a 64-wide panel:
    #   time "HH:00" at x=0..19, temp "XX°" at x=21..32, icon 6×6 at x=34..39,
    #   humidity "NN%"/"100%" at x=41..56. Total 57px used, 7px headroom.
    TIME_X, TEMP_X, ICON_X, HUMID_X = 0, 21, 34, 41

    for i, p in enumerate(periods[:5]):
        y_icon = 1 + i * 6
        y_text = y_icon + 5

        time_str = p.get("time") or ""
        graphics.DrawText(canvas, font, TIME_X, y_text, time_color, time_str)

        temp_hi  = p.get("temp_hi")
        temp_str = f"{temp_hi:>2}°" if isinstance(temp_hi, (int, float)) else " --"
        graphics.DrawText(canvas, font, TEMP_X, y_text, text_color, temp_str)

        _draw_weather_icon(canvas, p.get("icon", "cloud"), ICON_X, y_icon)

        humidity = p.get("humidity")
        if isinstance(humidity, (int, float)):
            graphics.DrawText(canvas, font, HUMID_X, y_text, humid_color, f"{int(round(humidity))}%")


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
        mode = snap.get("mode")
        if mode == "rotate":
            interval = max(3, int(snap.get("rotate_interval") or 20))
            sub = "weather" if int(time.time() / interval) % 2 == 1 else "tram"
            if sub == "weather":
                _render_weather(canvas, font, snap)
            else:
                _render_tram(canvas, font, board)
        elif mode == "text":
            _render_text(canvas, font_big, snap.get("text") or "", snap.get("text_changed_at") or 0.0)
        elif mode == "weather":
            _render_weather(canvas, font, snap)
        else:
            _render_tram(canvas, font, board)

        time.sleep(0.1)
        canvas = matrix.SwapOnVSync(canvas)
