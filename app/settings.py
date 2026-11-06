"""data/settings.json: thread-safe, atomic writes, tolerant reads, mtime-cached."""
import copy
import json
import os
import threading
import time
from . import paths
_lock = threading.RLock()
_cache: tuple[float, dict] | None = None
# a fresh install (no settings.json yet) starts with these: games are watched and their traffic goes through
# the bypass, the app comes up with Windows, full settings are shown. main.py calls first_run() at start.
FIRST_RUN = {"game_watch": True, "game_tcp": True, "game_udp": True, "autostart": True, "show_advanced": True}
def _file():
    return paths.DATA / "settings.json"
def load_settings(strict: bool = False) -> dict:
    """strict: raise OSError when the file exists but can't be read (a save must not write over it)."""
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
            text = p.read_text(encoding="utf-8-sig")  # tolerate BOM (Notepad, PowerShell)
        except OSError:  # locked by an antivirus/editor for a moment: not corrupt, keep the file
            if strict:
                raise
            return {}
        try:
            d = json.loads(text)
            if not isinstance(d, dict):
                raise ValueError("not a dict")
        except ValueError:
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
        for attempt in range(5):  # a read error here would save only kw and lose every other setting
            try:
                d = load_settings(strict=True) | kw
                break
            except OSError:
                if attempt == 4:
                    raise
                time.sleep(0.2)
        p = _file()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(json.dumps(d, indent=1, ensure_ascii=False, default=list))
            f.flush()
            os.fsync(f.fileno())  # a power cut right after the rename must not leave an empty file
        os.replace(tmp, p)
        _cache = None
def first_run() -> bool:
    """Write FIRST_RUN if there are no settings yet (fresh install). True if it did."""
    with _lock:
        if _file().exists():
            return False
        save_settings(**FIRST_RUN)
        return True
