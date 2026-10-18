"""Pick a strategy for a specific game by launching it with candidate strategies.

Generic site probes cannot tell whether a game's login gets through DPI, so this tries candidates for real:
start winws, launch the game, and treat it as logged in when the game process holds connections to
LOGIN_SERVERS+ distinct servers (stuck logins keep 1-2). Discord/YouTube must keep working as well.
"""
import os
import re
import subprocess
import time
from pathlib import Path

import psutil

from . import strategies, tester, winkeys
from .runner import Runner
from .settings import load_settings, save_settings

LOGIN_SERVERS = 5
WAIT = 75  # s per candidate


def launch_cmd(exe_path: str) -> str:
    """steam://rungameid/<id> for Steam games (they need the Steam launcher), else the exe itself."""
    p = Path(exe_path)
    parts = [x.lower() for x in p.parts]
    if "steamapps" in parts and "common" in parts:
        i = parts.index("common")
        installdir = p.parts[i + 1] if i + 1 < len(p.parts) else ""
        apps = Path(*p.parts[:parts.index("steamapps") + 1])
        for m in apps.glob("appmanifest_*.acf"):
            txt = m.read_text(encoding="utf-8", errors="replace")
            if re.search(r'"installdir"\s+"%s"' % re.escape(installdir), txt, re.I):
                return f"steam://rungameid/{re.search(r'appmanifest_(\d+)', m.name).group(1)}"
    return str(p)


def _game_procs(stem: str):
    stem = stem.lower()
    for p in psutil.process_iter(["name", "exe"]):
        n = (p.info["name"] or "").lower()
        if n.startswith(stem) or stem in (p.info["exe"] or "").lower():
            yield p


def _kill(stem: str) -> None:
    for p in _game_procs(stem):
        try:
            p.kill()
        except psutil.Error:
            pass


def _servers(stem: str) -> int:
    ips = set()
    for p in _game_procs(stem):
        try:
            ips |= {c.raddr.ip for c in p.net_connections("inet")
                    if c.raddr and c.status == "ESTABLISHED" and not c.raddr.ip.startswith("127.")}
        except psutil.Error:
            pass
    return len(ips)


def candidates() -> list[dict]:
    """Current choice first, then the best-scoring strategies and their profiles mixed into the current base."""
    cur = load_settings().get("strategy") or {}
    names = [s.name for s in strategies.load_all()]
    try:
        import json
        from . import paths
        res = json.loads((paths.DATA / "results.json").read_text(encoding="utf-8"))
        ranked = sorted((n for n in names if res.get(n)), key=lambda n: tester.score(res[n]), reverse=True)
    except (OSError, ValueError):
        ranked = names
    out = [cur] if cur else []
    gen, ips = "tcp:80,443:list-general", "tcp:80,443,8443:ipset-all"
    for n in ranked[:6]:
        out.append({"base": n, "picks": {}})
        if cur.get("base") and cur["base"] != n:
            out.append({"base": cur["base"], "picks": {**cur.get("picks", {}), gen: n, ips: n}})
    seen, uniq = set(), []
    for c in out:
        k = repr(sorted(c.get("picks", {}).items())) + c.get("base", "")
        if c.get("base") and k not in seen:
            seen.add(k)
            uniq.append(c)
    return uniq


def build(c: dict) -> strategies.Strategy:
    by = {s.name: s for s in strategies.load_all()}
    if c.get("picks"):
        return strategies.mix("mix", {g: by[n] for g, n in c["picks"].items() if n in by}, by[c["base"]])
    return by[c["base"]]


def pick_for_game(exe_path: str, runner: Runner, log=print, stop=lambda: False) -> dict | None:
    """Try candidates until the game logs in and Discord/YouTube still work. Saves and returns the winner."""
    stem = Path(exe_path).stem.split("-")[0]  # e.g. WardogsClient-Win64-Shipping -> WardogsClient
    stem = re.sub(r"(Client|Launcher)$", "", stem) or stem  # launcher + client of the same game
    cmd = launch_cmd(exe_path)
    s = load_settings()
    cands = candidates()
    log(f"Подбор для игры: {Path(exe_path).name}, вариантов: {len(cands)}. Игра будет сама запускаться и закрываться.")
    for i, c in enumerate(cands, 1):
        if stop():
            break
        _kill(stem)
        try:
            runner.start(build(c), True, True, s.get("auto_hostlist", True))
        except Exception as e:  # noqa: BLE001
            log(f"[{i}/{len(cands)}] не запустилась: {e}")
            continue
        time.sleep(2)
        os.startfile(cmd) if cmd.startswith("steam://") else subprocess.Popen([cmd], cwd=str(Path(cmd).parent))
        t0, best = time.time(), 0
        while time.time() - t0 < WAIT and not stop():
            winkeys.press_any_key(stem)  # many games wait for "press any key" before logging in
            best = max(best, _servers(stem))
            if best >= LOGIN_SERVERS:
                break
            time.sleep(2)
        name = c["base"] + (" + микс" if c.get("picks") else "")
        if best < LOGIN_SERVERS:
            log(f"[{i}/{len(cands)}] {name}: вход не прошёл ({best} серв.)")
            continue
        q = tester.quick_check()
        core = all(v for k, v in q.items() if k in ("Discord", "YouTube"))
        log(f"[{i}/{len(cands)}] {name}: вход прошёл за {time.time() - t0:.0f} с; Discord/YouTube: {'✓' if core else '✗'}")
        if core:
            save_settings(strategy=c, game_tcp=True, game_udp=True)
            log("Готово: стратегия подходит и для игры, и для Discord/YouTube. Можно играть.")
            return c
    _kill(stem)
    log("Не нашлось стратегии, с которой игра входит и Discord/YouTube работают. Для этой игры нужен VPN.")
    return None
