"""Persistent station configuration backed by a JSON file."""

from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import asdict, dataclass
from typing import Optional

log = logging.getLogger(__name__)

CONFIG_PATH = os.environ.get("TRAM_BOARD_CONFIG", "data/config.json")


@dataclass
class Config:
    station_query: str = ""    # free-text "<stop> <city>" entered by the admin
    stop_id:       str = ""    # cached EFA stop id (resolved from station_query)
    station_name:  str = ""    # resolved display name
    station_city:  str = ""    # resolved parent city


_lock = threading.Lock()
_cached: Optional[Config] = None


def load() -> Config:
    global _cached
    with _lock:
        if _cached is not None:
            return _cached
        if os.path.exists(CONFIG_PATH):
            try:
                with open(CONFIG_PATH, encoding="utf-8") as f:
                    data = json.load(f)
                _cached = Config(**{k: data.get(k, "") for k in Config.__annotations__})
            except Exception as e:
                log.warning("Could not read %s: %s — starting with empty config.", CONFIG_PATH, e)
                _cached = Config()
        else:
            _cached = Config()
        return _cached


def save(cfg: Config) -> None:
    global _cached
    with _lock:
        _cached = cfg
        os.makedirs(os.path.dirname(CONFIG_PATH) or ".", exist_ok=True)
        tmp = CONFIG_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(asdict(cfg), f, ensure_ascii=False, indent=2)
        os.replace(tmp, CONFIG_PATH)
        log.info("Wrote config to %s", CONFIG_PATH)
