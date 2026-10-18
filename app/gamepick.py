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


_FLOWS: "FlowCounter | None" = None


def _attempt(stem: str, cmd: str, stop) -> tuple[bool, int, float]:
    """Launch the game once (pressing keys while it loads); (logged_in, max_servers, seconds)."""
    _kill(stem)
    time.sleep(4)
    if _FLOWS:
        _FLOWS.reset()
    os.startfile(cmd) if cmd.startswith("steam://") else subprocess.Popen([cmd], cwd=str(Path(cmd).parent))
    t0, best = time.time(), 0
    while time.time() - t0 < WAIT and not stop():
        winkeys.press_any_key(stem)  # many games wait for "press any key" before logging in
        best = max(best, _servers(stem), _FLOWS.count() if _FLOWS else 0)
        if best >= LOGIN_SERVERS:
            break
        time.sleep(2)
    return best >= LOGIN_SERVERS, best, time.time() - t0


def _rate(stem: str, cmd: str, stop, tries: int) -> int:
    """Successful logins out of `tries` (stops early once a majority is decided)."""
    ok = 0
    for n in range(tries):
        if stop():
            break
        ok += _attempt(stem, cmd, stop)[0]
        if ok > tries // 2 or (n + 1 - ok) > tries // 2:
            break
    return ok


def pick_for_game(exe_path: str, runner: Runner, log=print, stop=lambda: False, tries: int = 3) -> dict | None:
    """Find a strategy the game logs in with (majority of `tries` launches) while Discord/YouTube keep working.

    Game logins are often flaky regardless of DPI, so every candidate is rated by repeated launches, and a
    no-bypass baseline is measured first: if the game logs in without zapret, the bypass only has to not break it.
    """
    stem = Path(exe_path).stem.split("-")[0]  # e.g. WardogsClient-Win64-Shipping -> WardogsClient
    stem = re.sub(r"(Client|Launcher)$", "", stem) or stem  # launcher + client of the same game
    cmd = launch_cmd(exe_path)
    s = load_settings()
    need = tries // 2 + 1
    global _FLOWS
    try:
        _FLOWS = FlowCounter(stem)
    except Exception:  # noqa: BLE001  (no WinDivert: psutil-only counting)
        _FLOWS = None
    try:
        return _pick(exe_path, stem, cmd, s, runner, log, stop, tries, need)
    finally:
        if _FLOWS:
            _FLOWS.stop()
            _FLOWS = None


def _pick(exe_path, stem, cmd, s, runner, log, stop, tries, need):
    log(f"Подбор для игры: {Path(exe_path).name}. Игра будет сама запускаться и закрываться, не трогайте её.")

    runner.stop()
    base_ok = _rate(stem, cmd, stop, tries)
    log(f"Без обхода: вход {base_ok} из {tries}")
    cands = candidates()
    for i, c in enumerate(cands, 1):
        if stop():
            break
        try:
            runner.start(build(c), True, True, s.get("auto_hostlist", True))
        except Exception as e:  # noqa: BLE001
            log(f"[{i}/{len(cands)}] не запустилась: {e}")
            continue
        time.sleep(2)
        ok = _rate(stem, cmd, stop, tries)
        name = c["base"] + (" + микс" if c.get("picks") else "")
        if ok < need:
            log(f"[{i}/{len(cands)}] {name}: вход {ok} из {tries} — не подходит")
            continue
        q = tester.quick_check()
        core = all(v for k, v in q.items() if k in ("Discord", "YouTube"))
        log(f"[{i}/{len(cands)}] {name}: вход {ok} из {tries}; Discord/YouTube: {'✓' if core else '✗'}")
        if core:
            _kill(stem)
            # a per-game profile: ZapretAuto switches to it while the game runs and back afterwards,
            # so the global strategy (Discord/YouTube) is not changed for the sake of one game
            profiles = {**load_settings().get("game_profiles", {}), Path(exe_path).name.lower(): c}
            extra = {} if load_settings().get("strategy") else {"strategy": c}
            save_settings(game_profiles=profiles, game_tcp=True, game_udp=True, **extra)
            if base_ok >= need:
                log("Готово. Этой игре обход не нужен — она входит и без него; выбранная стратегия ей не мешает. "
                    "Если вход иногда зависает — это сбои самой игры, просто перезапустите её.")
            else:
                log("Готово: стратегия подходит и для игры, и для Discord/YouTube. Она будет включаться сама, "
                    "пока игра запущена. Можно играть.")
            return c
    _kill(stem)
    if base_ok >= need:
        log("Игра входит без обхода, но любая стратегия ей мешает: добавьте игру в «Игнорировать».")
    else:
        log("Не нашлось стратегии, с которой игра входит и Discord/YouTube работают. Для этой игры нужен VPN.")
    return None


class FlowCounter:
    """Distinct remote servers per game seen by WinDivert's FLOW layer (sees unconnected UDP too, which
    psutil misses: Steam-networking and many shooters never connect() their UDP sockets)."""

    def __init__(self, stem: str):
        from .gamewatch import GameWatcher
        stem = stem.lower()
        seen: set[str] = set()

        class _W(GameWatcher):
            def _process(self, events):  # count every flow of the game, no game/ipset logic
                for pid, ip, _port, _proto in events:
                    exe, _ = self._proc(pid)
                    if exe and exe.startswith(stem) and "launcher" not in exe and not ip.startswith("127."):
                        seen.add(ip)

        self.seen = seen
        self.replied: set[str] = set()
        self._w = _W(lambda: None, log=lambda m: None, interval=1.0)
        self._w.start()
        self._h = None
        self._sniff_start()

    def _sniff_start(self) -> None:
        """Sniff inbound packets (NETWORK layer, read-only) to learn which servers actually answered:
        a UDP 'flow' exists as soon as the game sends, even if DPI drops every reply."""
        import ctypes
        import threading
        from . import paths
        try:
            dll = ctypes.WinDLL(str(paths.BIN / "WinDivert.dll"), use_last_error=True)
            dll.WinDivertOpen.restype = ctypes.c_void_p
            dll.WinDivertOpen.argtypes = [ctypes.c_char_p, ctypes.c_int, ctypes.c_int16, ctypes.c_uint64]
            dll.WinDivertRecv.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint,
                                          ctypes.POINTER(ctypes.c_uint), ctypes.c_void_p]
            dll.WinDivertRecv.restype = ctypes.c_int
            dll.WinDivertClose.argtypes = [ctypes.c_void_p]
            # layer NETWORK (0), flags SNIFF (1) | RECV_ONLY (4); only the 20-byte IPv4 header is needed
            h = dll.WinDivertOpen(b"inbound and ip and (tcp or udp)", 0, 0, 1 | 4)
            if not h or h == ctypes.c_void_p(-1).value:
                return
        except OSError:
            return
        self._dll, self._h = dll, h
        addr = (ctypes.c_ubyte * 128)()  # WINDIVERT_ADDRESS (80 bytes) with headroom

        def loop():
            buf = (ctypes.c_ubyte * 65535)()
            n = ctypes.c_uint()
            while self._h:
                if not dll.WinDivertRecv(h, buf, len(buf), ctypes.byref(n), addr):
                    if not self._h:
                        break
                    continue
                if n.value >= 20 and buf[0] >> 4 == 4:
                    self.replied.add(f"{buf[12]}.{buf[13]}.{buf[14]}.{buf[15]}")
                if len(self.replied) > 50000:
                    self.replied.clear()
        threading.Thread(target=loop, daemon=True, name="GamePickSniff").start()

    def reset(self) -> None:
        self.seen.clear()
        self.replied.clear()

    def count(self) -> int:
        """Game servers that answered (all flows if sniffing is unavailable)."""
        return len(self.seen & self.replied) if self._h else len(self.seen)

    def stop(self) -> None:
        self._w.stop()
        h, self._h = self._h, None
        if h:
            try:
                self._dll.WinDivertClose(h)
            except OSError:
                pass
