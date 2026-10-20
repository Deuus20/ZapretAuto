"""Small event history (data/stats.json) for the Statistics tab. Keeps 30 days."""
import json
import os
import threading
import time

from . import paths

_lock = threading.Lock()
KEEP = 30 * 86400


def _file():
    return paths.DATA / "stats.json"


def _load() -> list:
    try:
        d = json.loads(_file().read_text(encoding="utf-8"))
        return d if isinstance(d, list) else []
    except (OSError, ValueError):
        return []


def record(kind: str, **data) -> None:
    """kind: check (ok: bool) | repick (reason) | crash | start | stop | ipset | switch (to)."""
    with _lock:
        ev = [e for e in _load() if e.get("t", 0) > time.time() - KEEP]
        ev.append({"t": time.time(), "k": kind, **data})
        f = _file()
        f.parent.mkdir(parents=True, exist_ok=True)
        tmp = f.with_suffix(".tmp")
        tmp.write_text(json.dumps(ev), encoding="utf-8")
        os.replace(tmp, f)


def summary(days: int = 7) -> dict:
    since = time.time() - days * 86400
    ev = [e for e in _load() if e.get("t", 0) > since]
    checks = [e for e in ev if e["k"] == "check"]
    ok = sum(1 for e in checks if e.get("ok"))
    # bypass uptime: sum of start->stop intervals (open interval counts until now)
    up, started = 0.0, None
    for e in sorted(ev, key=lambda e: e["t"]):
        if e["k"] == "start" and started is None:
            started = e["t"]
        elif e["k"] in ("stop", "crash") and started is not None:
            up += e["t"] - started
            started = None
    if started is not None:
        up += time.time() - started
    repicks = [e for e in ev if e["k"] == "repick"]
    return {"days": days, "checks": len(checks), "ok_pct": round(100 * ok / len(checks)) if checks else None,
            "repicks": len(repicks), "last_repick": max((e["t"] for e in repicks), default=None),
            "crashes": sum(1 for e in ev if e["k"] == "crash"),
            "ipset": sum(1 for e in ev if e["k"] == "ipset"), "uptime_h": round(up / 3600, 1),
            "switches": [(e["t"], e.get("to", "?")) for e in ev if e["k"] == "switch"]}


def daily(days: int = 7) -> list[dict]:
    """Per local day, oldest first: background checks, failed ones, bypass crashes and re-picks (the home chart)."""
    today = time.localtime()
    start = time.mktime((today.tm_year, today.tm_mon, today.tm_mday - days + 1, 0, 0, 0, 0, 0, -1))
    out = []
    for i in range(days):
        t = time.localtime(start + i * 86400 + 3 * 3600)  # +3 h: a DST jump can't slip into the previous day
        out.append({"day": time.strftime("%d.%m", t), "checks": 0, "fails": 0, "crashes": 0, "repicks": 0})
    for e in _load():
        t = time.localtime(e.get("t", 0))
        key = time.strftime("%d.%m", t)
        d = next((d for d in out if d["day"] == key), None)
        if d is None or e.get("t", 0) < start:
            continue
        k = e.get("k")
        if k == "check":
            d["checks"] += 1
            d["fails"] += 0 if e.get("ok") else 1
        elif k == "crash":
            d["crashes"] += 1
        elif k == "repick":
            d["repicks"] += 1
    return out
