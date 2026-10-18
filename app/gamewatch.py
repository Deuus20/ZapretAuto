"""Detect game processes and collect their remote IPs into data/lists/ipset-games.txt."""
import ctypes
import ipaddress
import re
import subprocess
import threading
import time
from collections import defaultdict
from typing import Callable

import psutil

from . import paths
from .tester import load_settings, save_settings

PLACEHOLDER = "203.0.113.113/32"
SKIP_PORTS = {80, 443}
GAME_DIRS = ("steamapps\\common", "\\epic games\\", "\\riot games\\", "\\battle.net\\", "\\ubisoft",
             "\\ea games\\", "\\gog games\\", "\\xboxgames\\", "\\windowsapps\\")
NON_GAME_EXES = {
    "chrome.exe", "msedge.exe", "firefox.exe", "opera.exe", "brave.exe", "vivaldi.exe", "yandex.exe",
    "browser.exe", "discord.exe", "update.exe", "steam.exe", "steamwebhelper.exe", "steamservice.exe",
    "epicgameslauncher.exe", "epicwebhelper.exe", "eosoverlayrenderer-win64-shipping.exe",
    "riotclientservices.exe", "riotclientux.exe", "riotclientuxrender.exe", "battle.net.exe", "agent.exe",
    "ubisoftconnect.exe", "upc.exe", "uplaywebcore.exe", "eadesktop.exe", "origin.exe", "galaxyclient.exe",
    "svchost.exe", "system", "winws.exe", "python.exe", "pythonw.exe", "telegram.exe", "spotify.exe",
    "msedgewebview2.exe", "onedrive.exe", "teams.exe", "slack.exe", "gamingservices.exe",
    "gamingservicesnet.exe", "xboxpcapp.exe", "crashpad_handler.exe", "unitycrashhandler64.exe",
    "unitycrashhandler32.exe", "easyanticheat_eos_setup.exe", "vc_redist.x64.exe", "dxsetup.exe",
}
SYSTEM_DIRS = ("\\windows\\", "\\microsoft\\edge\\", "\\google\\chrome\\", "\\windowsapps\\microsoft.",
               "\\windowsapps\\microsoftcorporationii.", "\\windowsapps\\spotifyab.",
               "\\windowsapps\\5319275a.", "\\windowsapps\\telegram")
CREATE_NO_WINDOW = 0x08000000


def classify(path: str | None, settings: dict | None = None) -> bool:
    """True if process exe path looks like a game."""
    if not path:
        return False
    s = settings or {}
    p = path.lower().replace("/", "\\")
    exe = p.rsplit("\\", 1)[-1]
    if exe in {x.lower() for x in s.get("games_ignore", [])}:
        return False
    if exe in {x.lower() for x in s.get("games_force", [])}:
        return True
    if exe in NON_GAME_EXES:
        return False
    if any(d in p for d in SYSTEM_DIRS):
        return False
    return any(d in p for d in GAME_DIRS)


def public_ipv4(ip: str) -> bool:
    try:
        a = ipaddress.IPv4Address(ip)
    except ValueError:
        return False
    return a.is_global and not a.is_multicast


class _Addr(ctypes.Structure):
    """WINDIVERT_ADDRESS (2.2), FLOW view of the 64-byte union."""
    _fields_ = [("Timestamp", ctypes.c_int64), ("Bits", ctypes.c_uint32), ("Reserved2", ctypes.c_uint32),
                ("EndpointId", ctypes.c_uint64), ("ParentEndpointId", ctypes.c_uint64),
                ("ProcessId", ctypes.c_uint32), ("LocalAddr", ctypes.c_uint32 * 4),
                ("RemoteAddr", ctypes.c_uint32 * 4), ("LocalPort", ctypes.c_uint16),
                ("RemotePort", ctypes.c_uint16), ("Protocol", ctypes.c_uint8),
                ("_pad", ctypes.c_uint8 * 7)]


def _v4(a) -> str | None:
    w = list(a)
    # IPv4-mapped ::ffff:x.x.x.x stored as host-order words
    if w[1] == 0xFFFF and w[2] == 0 and w[3] == 0:
        v = w[0]
    elif w[2] == 0xFFFF and w[1] == 0 and w[0] == 0:
        v = w[3]
    else:
        return None
    return str(ipaddress.IPv4Address(v))


def _group(ips: set[str]) -> set[str]:
    by24 = defaultdict(list)
    for ip in ips:
        by24[ip.rsplit(".", 1)[0]].append(ip)
    out = set()
    for pre, lst in by24.items():
        if len(lst) >= 4:
            out.add(f"{pre}.0/24")
        else:
            out.update(lst)
    return out


class GameWatcher(threading.Thread):
    def __init__(self, on_change: Callable[[], None], log: Callable[[str], None] = print, interval: float = 3.0):
        super().__init__(daemon=True, name="GameWatcher")
        self.on_change, self.log, self.interval = on_change, log, interval
        self._stop_ev = threading.Event()
        self._lock = threading.Lock()
        self._games: dict[str, dict] = {}
        self._events: list[tuple[int, str, int]] = []
        self._ev_lock = threading.Lock()
        self._handle = None
        self._dll = None
        self.flow_count = 0
        self._pending_since: float | None = None
        self._last_written: str | None = None
        self._pid_cache: dict[int, tuple[str | None, str | None]] = {}
        self._pinging: set[str] = set()
        self._ping: dict[str, float | None] = {}
        for exe, g in load_settings().get("games", {}).items():
            self._games[exe] = {"path": g.get("path", ""), "ips": set(g.get("ips", [])),
                                "active": False, "ping_ms": None}
        try:
            self._last_written = (paths.LISTS / "ipset-games.txt").read_text(encoding="utf-8")
        except OSError:
            pass

    # ---- public
    def stop(self) -> None:
        self._stop_ev.set()
        h, dll = self._handle, self._dll
        if h is not None and dll is not None:
            self._handle = None
            try:
                dll.WinDivertClose(h)
            except Exception:
                pass

    def games(self) -> dict[str, dict]:
        with self._lock:
            return {k: {"path": v["path"], "ips": set(v["ips"]), "active": v["active"],
                        "ping_ms": v["ping_ms"]} for k, v in self._games.items()}

    # ---- flow capture
    def _open_divert(self) -> bool:
        try:
            dll = ctypes.WinDLL(str(paths.BIN / "WinDivert.dll"), use_last_error=True)
            dll.WinDivertOpen.restype = ctypes.c_void_p
            dll.WinDivertOpen.argtypes = [ctypes.c_char_p, ctypes.c_int, ctypes.c_int16, ctypes.c_uint64]
            dll.WinDivertRecv.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint,
                                          ctypes.c_void_p, ctypes.POINTER(_Addr)]
            dll.WinDivertRecv.restype = ctypes.c_int
            dll.WinDivertClose.argtypes = [ctypes.c_void_p]
            h = dll.WinDivertOpen(b"true", 2, 0, 1 | 4)
            if h in (None, ctypes.c_void_p(-1).value):
                self.log(f"gamewatch: WinDivertOpen failed (err {ctypes.get_last_error()})")
                return False
            self._dll, self._handle = dll, h
            return True
        except Exception as e:
            self.log(f"gamewatch: WinDivert unavailable: {e}")
            return False

    def _recv_loop(self) -> None:
        a = _Addr()
        dll, h = self._dll, self._handle
        while not self._stop_ev.is_set() and self._handle is not None:
            if not dll.WinDivertRecv(h, None, 0, None, ctypes.byref(a)):
                break
            event = (a.Bits >> 8) & 0xFF
            outbound = (a.Bits >> 17) & 1
            if event != 1 or not outbound or a.Protocol not in (6, 17):
                continue
            ip = _v4(a.RemoteAddr)
            if ip is None:
                continue
            self.flow_count += 1
            with self._ev_lock:
                self._events.append((a.ProcessId, ip, a.RemotePort))
                del self._events[:-5000]

    def _poll_psutil(self) -> list[tuple[int, str, int]]:
        out = []
        try:
            for c in psutil.net_connections("inet"):
                if c.raddr and c.pid and c.family.name == "AF_INET":
                    out.append((c.pid, c.raddr.ip, c.raddr.port))
        except Exception as e:
            self.log(f"gamewatch: psutil failed: {e}")
        return out

    # ---- logic
    def _proc(self, pid: int) -> tuple[str | None, str | None]:
        if pid in self._pid_cache:
            return self._pid_cache[pid]
        try:
            p = psutil.Process(pid)
            r = (p.name().lower(), p.exe())
        except Exception:
            return (None, None)
        self._pid_cache[pid] = r
        if len(self._pid_cache) > 2000:
            self._pid_cache.clear()
        return r

    def _ping_ip(self, ip: str) -> None:
        ms = None
        try:
            out = subprocess.run(["ping", "-n", "2", "-w", "800", ip], capture_output=True, timeout=10,
                                 creationflags=CREATE_NO_WINDOW).stdout.decode("cp866", "ignore")
            last = [ln for ln in out.splitlines() if "=" in ln]
            m = re.search(r"(\d+)\D{0,4}$", last[-1].strip()) if last else None
            ms = float(m.group(1)) if m and "TTL" not in last[-1] and "ttl" not in last[-1].lower() else None
        except Exception:
            pass
        with self._lock:
            self._ping[ip] = ms
            for g in self._games.values():
                if ip in g["ips"]:
                    vals = [self._ping[i] for i in g["ips"] if self._ping.get(i) is not None]
                    g["ping_ms"] = min(vals) if vals else None
        self._pinging.discard(ip)

    def _process(self, events: list[tuple[int, str, int]]) -> None:
        settings = load_settings()
        seen_active: set[str] = set()
        new_ip = False
        for pid, ip, port in events:
            if port in SKIP_PORTS or not public_ipv4(ip):
                continue
            exe, path = self._proc(pid)
            if not exe or not classify(path, settings):
                continue
            seen_active.add(exe)
            with self._lock:
                g = self._games.setdefault(exe, {"path": path, "ips": set(), "active": True, "ping_ms": None})
                g["path"] = path
                if ip not in g["ips"]:
                    g["ips"].add(ip)
                    new_ip = True
            if ip not in self._ping and ip not in self._pinging:
                self._pinging.add(ip)
                threading.Thread(target=self._ping_ip, args=(ip,), daemon=True).start()
        with self._lock:
            for exe, g in self._games.items():
                g["active"] = exe in seen_active or (g["active"] and self._alive(exe))
        if new_ip:
            self._pending_since = time.time()
            self._persist_settings()

    @staticmethod
    def _alive(exe: str) -> bool:
        try:
            return any((p.info["name"] or "").lower() == exe for p in psutil.process_iter(["name"]))
        except Exception:
            return False

    def _persist_settings(self) -> None:
        with self._lock:
            data = {k: {"path": v["path"], "ips": sorted(v["ips"])} for k, v in self._games.items()}
        save_settings(games=data)

    def _render(self) -> str:
        with self._lock:
            ips = set().union(*[g["ips"] for g in self._games.values()]) if self._games else set()
        lines = sorted(_group(ips), key=lambda x: tuple(int(o) for o in re.split(r"[./]", x)[:4]))
        man = paths.DATA / "games_manual.txt"
        if man.exists():
            for ln in man.read_text(encoding="utf-8").splitlines():
                ln = ln.strip()
                if ln and not ln.startswith("#") and ln not in lines:
                    lines.append(ln)
        if not lines:
            lines = [PLACEHOLDER]
        return "\n".join(lines) + "\n"

    def _flush(self) -> None:
        text = self._render()
        if text == self._last_written:
            return
        paths.LISTS.mkdir(parents=True, exist_ok=True)
        (paths.LISTS / "ipset-games.txt").write_text(text, encoding="utf-8")
        self._last_written = text
        try:
            self.on_change()
        except Exception as e:
            self.log(f"gamewatch: on_change error: {e}")

    def run(self) -> None:
        use_divert = self._open_divert()
        if use_divert:
            threading.Thread(target=self._recv_loop, daemon=True, name="GameWatchRecv").start()
            self.log("gamewatch: WinDivert flow capture active")
        else:
            self.log("gamewatch: fallback to psutil.net_connections (TCP/connected UDP only)")
        while not self._stop_ev.wait(self.interval):
            try:
                if use_divert:
                    with self._ev_lock:
                        ev, self._events = self._events, []
                else:
                    ev = self._poll_psutil()
                self._process(ev)
                if self._pending_since and time.time() - self._pending_since >= 10:
                    self._pending_since = None
                    self._flush()
            except Exception as e:
                self.log(f"gamewatch: loop error: {e}")
