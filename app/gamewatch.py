"""Detect game processes and collect their remote IPs into data/lists/ipset-games.txt."""
import ctypes
import ipaddress
import json
import os
import re
import statistics
import subprocess
import socket
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

import psutil

from . import known_games, paths
from .settings import load_settings, save_settings

PLACEHOLDER = "203.0.113.113/32"
SKIP_PORTS = {80, 443}  # web/CDN traffic (launchers, login APIs) is covered by hostlists/ipset; desyncing it broke launchers
MAX_WEB_NETS = 512      # /24s of games' web/login servers kept out of the ipset-all profiles
# known login servers, so even a game's first launch logs in (the rest is learned from traffic)
KNOWN_WEB_NETS = set(known_games.WEB_NETS) | {"3.164.206.0/24", "3.171.214.0/24", "104.18.124.0/24",
                                                "104.18.125.0/24"}  # Wardogs login
KNOWN_WEB_HOSTS = ("game.live.wardogs.bulkhead.pragmaengine.com",)  # CloudFront: resolved at start, IPs rotate
LOGIN_GRACE = 120.0  # s after a game starts: no winws restarts, so logins are not cut off
GAME_DIRS = ("steamapps\\common", "\\epic games\\", "\\riot games\\", "\\battle.net\\", "\\ubisoft",
             "\\ea games\\", "\\gog games\\", "\\xboxgames\\")
# not \windowsapps\: every Microsoft Store app lives there (Claude, WhatsApp, ...); Store games are caught by
# their UDP traffic (_udp_game) like any other game outside launcher folders
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
# apps with lots of UDP to public IPs that are not games
UDP_NON_GAME_EXES = {
    "qbittorrent.exe", "utorrent.exe", "bittorrent.exe", "transmission-qt.exe", "deluge.exe", "tixati.exe",
    "zoom.exe", "skype.exe", "whatsapp.exe", "viber.exe", "ms-teams.exe", "anydesk.exe", "teamviewer.exe",
    "rustdesk.exe", "parsecd.exe", "obs64.exe", "nvcontainer.exe", "amneziavpn.exe", "amneziavpn-service.exe",
    "wireguard.exe", "openvpn.exe", "radmin_vpn.exe", "rvcontrolsvc.exe", "hamachi-2.exe", "zerotier-one_x64.exe",
    "nekoray.exe", "v2rayn.exe", "xray.exe", "sing-box.exe", "hiddify.exe", "claude.exe", "node.exe",
}
UDP_GAME_MIN_IPS = 3     # distinct public UDP peers before an unknown exe is treated as a game
DEBOUNCE = 5.0           # s of quiet after the last new IP before rewriting the ipset
MIN_RESTART_GAP = 5.0    # s between ipset rewrites (winws reloads the file itself, no restart needed)
MAX_IPS_PER_GAME = 256
PID_TTL = 30.0           # s; Windows reuses PIDs, so cached exe names expire
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
    if exe in {x.lower().replace("/", "\\").rsplit("\\", 1)[-1] for x in s.get("games_force", [])}:
        return True  # entries may be exe names or full paths
    if exe in {x.lower() for x in s.get("games", {})} and "launcher" not in exe:
        return True  # detected earlier (e.g. by UDP heuristic)
    if exe in NON_GAME_EXES or "launcher" in exe:
        return False
    if any(d in p for d in SYSTEM_DIRS):
        return False
    return any(d in p for d in GAME_DIRS)


def display_name(exe: str, path: str | None = "") -> str:
    """Human name of a game: its Steam/Epic folder ("Wardogs"), else the exe without build suffixes."""
    p = (path or "").replace("/", "\\")
    m = re.search(r"\\(?:steamapps\\common|epic games|riot games|gog games|xboxgames)\\([^\\]+)\\", p, re.I)
    if m:
        return m.group(1)
    base = p.rsplit("\\", 1)[-1]
    if base.lower() == exe.lower():
        exe = base  # the path keeps the original case ("MyGame.exe"); keys are lower-case
    name = exe[:-4] if exe.lower().endswith(".exe") else exe
    name = re.sub(r"(?i)([-_](win(32|64)|x64|x86|shipping|dx1[12]|vulkan))+$", "", name)
    name = re.sub(r"(?i)client$", "", name) or name
    return name[:1].upper() + name[1:]


def web_client(path: str | None, settings: dict | None = None) -> bool:
    """Game or its launcher (anything in a game dir), or a program the user said the bypass breaks
    (settings apps_exclude): its 80/443 servers must not be desynced."""
    p = (path or "").lower().replace("/", "\\")
    exe = p.rsplit("\\", 1)[-1]
    if exe and exe in {x.lower() for x in (settings or {}).get("apps_exclude", [])}:
        return True
    if classify(path, settings):
        return True
    return (exe not in NON_GAME_EXES and exe not in {x.lower() for x in (settings or {}).get("games_ignore", [])}
            and not any(d in p for d in SYSTEM_DIRS) and any(d in p for d in GAME_DIRS))


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
    """Collapse >=4 IPs of one /24 into the /24."""
    by24 = defaultdict(list)
    out = set()
    for ip in ips:
        by24[ip.rsplit(".", 1)[0]].append(ip)
    for pre, lst in by24.items():
        if len(lst) >= 4:
            out.add(f"{pre}.0/24")
        else:
            out.update(lst)
    return out


def tcp_latency(ip: str, port: int, timeout: float = 2.0, n: int = 3) -> float | None:
    """Median TCP connect time in ms over n tries; None if no try succeeded."""
    vals = []
    for _ in range(n):
        t0 = time.perf_counter()
        try:
            with socket.create_connection((ip, port), timeout=timeout):
                vals.append((time.perf_counter() - t0) * 1000)
        except OSError:
            pass
    return statistics.median(vals) if len(vals) >= (n // 2 + 1) else None


def is_degraded(before: float | None, after: float | None) -> bool:
    """True if TCP connect got worse after desync. No baseline = no judgement."""
    if before is None:
        return False
    if after is None:
        return True
    return after > before * 1.5 and after - before > 30


class GameWatcher(threading.Thread):
    def __init__(self, on_change: Callable[[], None], log: Callable[[str], None] = print, interval: float = 3.0):
        super().__init__(daemon=True, name="GameWatcher")
        self.on_change, self.log, self.interval = on_change, log, interval
        self._stop_ev = threading.Event()
        self._lock = threading.Lock()
        self._games: dict[str, dict] = {}
        self._udp_hits: dict[str, set[str]] = {}
        self._events: list[tuple[int, str, int]] = []
        self._ev_lock = threading.Lock()
        self._handle = None
        self._dll = None
        self._pending_since: float | None = None
        self._last_written: str | None = None
        self._last_web: str | None = None
        self._pid_cache: dict[int, tuple[tuple[str | None, str | None], float]] = {}
        self._pool = ThreadPoolExecutor(4, thread_name_prefix="gamewatch")
        self._flush_lock = threading.Lock()
        self._last_restart = 0.0
        self._pinging: set[str] = set()
        self._ping: dict[str, float | None] = {}
        self._base: dict[str, tuple[int, float, str]] = {}  # ip -> (port, ms, exe) baseline before desync
        self._measuring: set[str] = set()
        self._checked: dict = {}
        self._db_games: dict[str, set[str]] = {}  # shared base (games-db.json): servers others have seen
        self.rollback_delay = 15.0
        self.measure = tcp_latency
        st = load_settings()
        if st.get("games_schema", 1) < 2:  # v1 collected 80/443 CDN addresses too: start over once
            save_settings(games={k: {"path": v.get("path", ""), "ips": []} for k, v in st.get("games", {}).items()},
                          games_schema=2)
        st = load_settings()
        if st.get("games_schema", 1) < 3:  # v2 took every Microsoft Store app for a game: real ones come back by UDP
            forced = {x.lower().replace("/", "\\").rsplit("\\", 1)[-1] for x in st.get("games_force", [])}
            save_settings(games={k: v for k, v in st.get("games", {}).items()
                                 if k in forced or "\\windowsapps\\" not in v.get("path", "").lower()},
                          games_schema=3)
        self._web_nets: set[str] = set(load_settings().get("games_web", [])) | KNOWN_WEB_NETS
        for exe, g in load_settings().get("games", {}).items():
            self._games[exe] = {"path": g.get("path", ""), "ips": set(g.get("ips", [])),
                                "active": False, "ping_ms": None}
        try:
            self._last_written = (paths.LISTS / "ipset-games.txt").read_text(encoding="utf-8")
            self._last_web = (paths.LISTS / "ipset-exclude-games.txt").read_text(encoding="utf-8")
        except OSError:
            pass

    # ---- public
    def stop(self) -> None:
        self._stop_ev.set()
        self._pool.shutdown(wait=False, cancel_futures=True)
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
                        "ping_ms": v["ping_ms"], "started": v.get("started", 0)} for k, v in self._games.items()}

    def web_nets(self) -> list[str]:
        """Login/web nets learned on this PC (for sharing; shipped and shared ones are left out)."""
        with self._lock:
            return sorted(self._web_nets - KNOWN_WEB_NETS)

    def _seed(self, exe: str) -> set[str]:
        return set(known_games.GAME_IPS.get(exe, ())) | self._db_games.get(exe, set())

    def add_manual(self, path: str) -> str:
        """Add a game picked by hand (shown at once; its servers are collected when it runs)."""
        exe = path.replace("/", "\\").rsplit("\\", 1)[-1].lower()
        with self._lock:
            g = self._games.setdefault(exe, {"path": path, "ips": set(), "active": False, "ping_ms": None})
            g["path"] = path
        self._persist_settings()
        return exe

    def reset_game(self, exe: str) -> None:
        """Drop all learned IPs of a game."""
        with self._lock:
            g = self._games.get(exe)
            if not g:
                return
            for ip in g["ips"]:
                self._base.pop(ip, None)
            g["ips"].clear()
        self._persist_settings()
        self._flush()

    def _measure_base(self, ip: str, port: int, exe: str) -> None:
        try:
            ms = self.measure(ip, port)
            if ms is not None:
                with self._lock:
                    self._base[ip] = (port, ms, exe)
        finally:
            with self._lock:
                self._measuring.discard(ip)

    def _schedule_rollback(self) -> None:
        with self._lock:
            todo = {ip: v for ip, v in self._base.items() if ip not in self._checked}
            self._checked.update(todo)
        if todo:
            threading.Thread(target=self._rollback_check, args=(todo,), daemon=True).start()

    def _rollback_check(self, todo: dict[str, tuple[int, float, str]]) -> None:
        if self._stop_ev.wait(self.rollback_delay):
            return
        bad = []
        for ip, (port, before, exe) in todo.items():
            with self._lock:
                if ip not in self._games.get(exe, {}).get("ips", ()):
                    continue
            after = self.measure(ip, port)
            if is_degraded(before, after):
                bad.append((ip, exe))
        if not bad:
            return
        s = load_settings()
        banned = set(s.get("games_bad_ips", []))
        with self._lock:
            for ip, exe in bad:
                banned.add(ip)
                self._games[exe]["ips"].discard(ip)
                self.log(f"откат IP {ip} для {exe}")
        save_settings(games_bad_ips=sorted(banned))
        self._persist_settings()
        self._flush()

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
            with self._ev_lock:
                self._events.append((a.ProcessId, ip, a.RemotePort, a.Protocol))
                del self._events[:-5000]

    def _poll_psutil(self) -> list[tuple[int, str, int, int]]:
        out = []
        try:
            for c in psutil.net_connections("inet"):
                if c.raddr and c.pid and c.family.name == "AF_INET":
                    out.append((c.pid, c.raddr.ip, c.raddr.port, 17 if c.type == socket.SOCK_DGRAM else 6))
        except Exception as e:
            self.log(f"gamewatch: psutil failed: {e}")
        return out

    # ---- logic
    def _proc(self, pid: int) -> tuple[str | None, str | None]:
        hit = self._pid_cache.get(pid)
        if hit and time.time() - hit[1] < PID_TTL:
            return hit[0]
        try:
            p = psutil.Process(pid)
            r = (p.name().lower(), p.exe())
        except Exception:
            return (None, None)
        self._pid_cache[pid] = (r, time.time())
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

    def _udp_game(self, exe: str, path: str, ip: str, port: int, proto: int, settings: dict) -> bool:
        """Heuristic for games outside known launcher dirs: UDP to several public peers on high ports."""
        p = (path or "").lower()
        if (proto != 17 or port < 1024 or exe in NON_GAME_EXES or exe in UDP_NON_GAME_EXES
                or any(d in p for d in SYSTEM_DIRS)
                or exe in {x.lower() for x in settings.get("games_ignore", [])}):
            return False
        hits = self._udp_hits.setdefault(exe, set())
        hits.add(ip)
        if len(hits) >= UDP_GAME_MIN_IPS:
            if exe not in self._games:
                self.log(f"игра определена по UDP-трафику: {exe}")
            return True
        return False

    def _process(self, events: list[tuple[int, str, int, int]]) -> None:
        settings = load_settings()
        seen_active: set[str] = set()
        new_ip = new_web = False
        banned = set(settings.get("games_bad_ips", []))
        if len(self._udp_hits) > 500:
            self._udp_hits.clear()
        if len(self._ping) > 5000:
            self._ping.clear()
        for pid, ip, port, proto in set(events):
            if port in SKIP_PORTS and public_ipv4(ip):
                net = ip.rsplit(".", 1)[0] + ".0/24"
                exe, path = self._proc(pid)
                if exe in self._games and classify(path, settings):
                    seen_active.add(exe)  # a game logging in talks only 443: it runs (grace, no updates)
                if (exe and net not in self._web_nets and len(self._web_nets) < MAX_WEB_NETS
                        and web_client(path, settings)):
                    with self._lock:
                        self._web_nets.add(net)
                    new_web = True
                continue
            if not public_ipv4(ip) or ip in banned:
                continue
            exe, path = self._proc(pid)
            if not exe or not (classify(path, settings) or self._udp_game(exe, path, ip, port, proto, settings)):
                continue
            seen_active.add(exe)
            with self._lock:
                if exe not in self._games:  # first sight of a known game: its servers are known already
                    new_ip = new_ip or bool(self._seed(exe))
                g = self._games.setdefault(exe, {"path": path, "ips": set(list(self._seed(exe))[:MAX_IPS_PER_GAME]),
                                                 "active": True, "ping_ms": None})
                g.setdefault("started", time.time())
                g["path"] = path
                if ip not in g["ips"] and len(g["ips"]) < MAX_IPS_PER_GAME:
                    g["ips"].add(ip)
                    new_ip = True
                    if ip not in self._base and ip not in self._measuring and ip not in self._checked:
                        self._measuring.add(ip)
                        self._pool.submit(self._measure_base, ip, port, exe)
            if ip not in self._ping and ip not in self._pinging:
                self._pinging.add(ip)
                self._pool.submit(self._ping_ip, ip)
        with self._lock:
            names = None
            for exe, g in self._games.items():
                if exe in seen_active:
                    if not g["active"]:
                        g["started"] = time.time()  # (re)launched: give its login LOGIN_GRACE without restarts
                    g["active"] = True
                elif g["active"]:
                    if names is None:  # one process scan per tick, only when needed
                        names = self._running_names()
                    g["active"] = exe in names
        if new_ip or new_web:
            self._pending_since = time.time()
            self._persist_settings()

    @staticmethod
    def _running_names() -> set[str]:
        try:
            return {(p.info["name"] or "").lower() for p in psutil.process_iter(["name"])}
        except Exception:
            return set()

    def _persist_settings(self) -> None:
        with self._lock:
            data = {k: {"path": v["path"], "ips": sorted(v["ips"])} for k, v in self._games.items()}
            web = sorted(self._web_nets)
        save_settings(games=data, games_web=web)

    def _render_web(self) -> str:
        """ipset-exclude-games.txt; settings games_web_bypass=true keeps desyncing them (opt-out)."""
        with self._lock:
            nets = sorted(self._web_nets, key=lambda x: tuple(int(o) for o in re.split(r"[./]", x)[:4]))
        if not nets or load_settings().get("games_web_bypass"):
            nets = [PLACEHOLDER]
        return "\n".join(nets) + "\n"

    @staticmethod
    def _write(name: str, text: str) -> None:
        paths.LISTS.mkdir(parents=True, exist_ok=True)
        f = paths.LISTS / name
        tmp = f.with_suffix(".tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, f)  # winws must never read a half-written list

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
        with self._flush_lock:
            web = self._render_web()
            if web != self._last_web:
                self._write("ipset-exclude-games.txt", web)
                self._last_web = web
            text = self._render()
            if text == self._last_written:
                return
            self._write("ipset-games.txt", text)
            self._last_written = text
            self._last_restart = time.time()
        try:
            self.on_change()
        except Exception as e:
            self.log(f"gamewatch: on_change error: {e}")
        self._schedule_rollback()

    def _resolve_known(self) -> None:
        nets = set()
        for host in KNOWN_WEB_HOSTS:
            try:
                for *_, sa in socket.getaddrinfo(host, 443, socket.AF_INET, socket.SOCK_STREAM):
                    nets.add(sa[0].rsplit(".", 1)[0] + ".0/24")
            except OSError:
                pass
        db = self._shared_db()
        nets |= set(db.get("web", []))
        with self._lock:
            new = nets - self._web_nets
            if len(self._web_nets) < MAX_WEB_NETS:
                self._web_nets |= new
            self._db_games = {exe.lower(): set(ips) for exe, ips in db.get("games", {}).items()}
            for exe, g in self._games.items():  # games already known here get the servers others have seen
                add = self._db_games.get(exe, set()) - g["ips"]
                if add and len(g["ips"]) < MAX_IPS_PER_GAME:
                    g["ips"] |= set(list(add)[:MAX_IPS_PER_GAME - len(g["ips"])])
                    new = True
        if new:
            self._persist_settings()
            self._pending_since = time.time()

    @staticmethod
    def _shared_db() -> dict:
        """games-db.json from the repo; the last good copy when offline. Only valid public IPv4 entries pass."""
        from . import sharedb
        cache = paths.DATA / "games-db.json"
        db = sharedb.games_db()
        if db is not None:
            try:
                cache.write_text(json.dumps(db), encoding="utf-8")
            except OSError:
                pass
        else:
            try:
                db = json.loads(cache.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return {}
        games = {exe: [ip for ip in ips if public_ipv4(ip)][:MAX_IPS_PER_GAME]
                 for exe, ips in (db.get("games") or {}).items() if isinstance(ips, list)}
        web = [n for n in (db.get("web") or []) if isinstance(n, str) and n.endswith(".0/24") and public_ipv4(n[:-3])]
        return {"games": games, "web": web[:MAX_WEB_NETS]}

    def run(self) -> None:
        self._pending_since = time.time()  # write ipset-exclude-games.txt with the known nets right away
        threading.Thread(target=self._resolve_known, daemon=True, name="GameWatchDNS").start()
        use_divert = self._open_divert()
        if use_divert:
            threading.Thread(target=self._recv_loop, daemon=True, name="GameWatchRecv").start()
            self.log("gamewatch: WinDivert flow capture active")
        else:
            self.log("gamewatch: fallback to psutil.net_connections (TCP/connected UDP only)")
        tick = 0
        while not self._stop_ev.wait(self.interval):
            try:
                if use_divert:
                    with self._ev_lock:
                        ev, self._events = self._events, []
                    if tick % 20 == 0:  # flow layer sees only new flows: pick up already-open connections (~1/min)
                        ev += self._poll_psutil()
                    tick += 1
                else:
                    ev = self._poll_psutil()
                self._process(ev)
                now = time.time()
                if (self._pending_since and now - self._pending_since >= DEBOUNCE
                        and now - self._last_restart >= MIN_RESTART_GAP):
                    self._pending_since = None
                    self._flush()
            except Exception as e:
                self.log(f"gamewatch: loop error: {e}")
