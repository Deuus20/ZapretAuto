"""data/settings.json: thread-safe, atomic writes, tolerant reads, mtime-cached."""
import copy
import json
import os
import threading

from . import paths

_lock = threading.RLock()
_cache: tuple[float, dict] | None = None


# a fresh install (no settings.json yet) starts with these: games are watched and their traffic goes through
# the bypass, the app comes up with Windows, full settings are shown. main.py calls first_run() at start.
FIRST_RUN = {"game_watch": True, "game_tcp": True, "game_udp": True, "autostart": True, "show_advanced": True}


def _file():
    return paths.DATA / "settings.json"


def load_settings() -> dict:
    global _cache
    p = _file()
    with _lock:
        try:
            m = p.stat().st_mtime
        except OSError:
            return {}
        if _cache and _cache[0] == m:
            return copy.deepcopy(_cache[1])  # callers edit nested dicts (strategy, games)
        try:
            d = json.loads(p.read_text(encoding="utf-8-sig"))  # tolerate BOM (Notepad, PowerShell)
            if not isinstance(d, dict):
                raise ValueError("not a dict")
        except (OSError, ValueError):
            try:
                os.replace(p, p.with_suffix(".json.bad"))  # keep the broken file for inspection
            except OSError:
                pass
            return {}
        _cache = (m, d)
        return copy.deepcopy(d)


def save_settings(**kw) -> None:
    global _cache
    with _lock:
        d = load_settings() | kw
        p = _file()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(d, indent=1, ensure_ascii=False, default=list), encoding="utf-8")
        os.replace(tmp, p)
        _cache = None


def first_run() -> bool:
    """Write FIRST_RUN if there are no settings yet (fresh install). True if it did."""
    with _lock:
        if _file().exists():
            return False
        save_settings(**FIRST_RUN)
        return True
