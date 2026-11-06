"""Start/stop winws.exe (single instance)."""
import ctypes
import hashlib
import os
import shutil
import subprocess
import threading
from pathlib import Path
import psutil
from . import paths, vpn
from .strategies import Strategy
import contextlib
EXES = ("winws.exe", "winws2.exe")  # winws2: experimental zapret2 mode
CREATE_NO_WINDOW = 0x08000000
ABOVE_NORMAL_PRIORITY_CLASS = 0x00008000  # every captured game packet waits for winws: don't queue it behind the game
def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except OSError:
        return False
def _stray_winws() -> bool:
    try:
        return any((p.info["name"] or "").lower() in EXES for p in psutil.process_iter(["name"]))
    except psutil.Error:
        return True
class Runner:
    def __init__(self) -> None:
        self.proc: subprocess.Popen | None = None
        self.current: Strategy | None = None
        self._out = None  # winws output file of the current run
        self._lock = threading.RLock()
        self.vpn_ips: set[str] = set()  # VPN servers left out of the capture filter at start
        self.unwatched: dict[Path, list[str] | None] = {}  # list files winws doesn't reload (slim())
    @property
    def running(self) -> bool:
        p = self.proc
        return p is not None and p.poll() is None
    def start(self, s: Strategy, game_tcp: bool = False, game_udp: bool = False,
              auto_hostlist: bool = False, skip: set[str] = frozenset()) -> None:
        """skip: servers of games switched off by hand, left out of the capture like VPN servers."""
        with self._lock:
            self.stop()
            paths.sync_lists()
            try:
                self.vpn_ips = vpn.write_exclude()
            except Exception:  # noqa: BLE001
                self.vpn_ips = set()
            argv, self.unwatched = slim(s.argv(game_tcp, game_udp, auto_hostlist))
            # VPN servers first: only 64 fit, and a dropped one put the whole tunnel back through winws
            argv = uncapture(argv, sorted(self.vpn_ips) + sorted(set(skip) - self.vpn_ips))
            log = output_file()
            log.parent.mkdir(parents=True, exist_ok=True)
            self._out = open(log, "wb")  # a file, not a PIPE: an undrained pipe would block winws
            self.proc = subprocess.Popen(
                argv, cwd=Path(argv[0]).parent, creationflags=CREATE_NO_WINDOW | ABOVE_NORMAL_PRIORITY_CLASS,
                stdout=self._out, stderr=subprocess.STDOUT,
            )
            self.current = s
    def stop(self) -> None:
        with self._lock:
            p = self.proc
            if p is not None and p.poll() is None:
                p.terminate()
                try:
                    p.wait(5)
                except subprocess.TimeoutExpired:
                    p.kill()
            if _stray_winws():  # instances started by upstream .bat files or a previous run
                for exe in EXES:
                    with contextlib.suppress(subprocess.TimeoutExpired):
                        subprocess.run(["taskkill", "/F", "/IM", exe], capture_output=True,
                                       creationflags=CREATE_NO_WINDOW, timeout=10)
            self.proc = self.current = None
            if getattr(self, "_out", None):
                self._out.close()
                self._out = None
PLACEHOLDERS = {"203.0.113.113/32", "domain.example.abc"}  # paths.USER_LISTS fillers: winws refuses empty lists
INLINE_MAX = 200  # entries: an exclude list this small goes on the command line instead of a file
LIVE = ("ipset-all.txt", "ipset-games.txt", "list-auto.txt")  # big or rewritten on the fly: stay files
def _entries(f: Path) -> list[str] | None:
    try:
        lines = f.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return None
    return [e for e in (ln.split("#")[0].strip() for ln in lines) if e]
def slim(argv: list[str]) -> tuple[list[str], dict[Path, list[str] | None]]:
    """Fewer list files for winws. For every NEW connection winws (a Cygwin build) checks each list file of each
    profile for changes: 56 references cost ~3 ms per new flow, so a torrent client opening dozens of connections
    a second queued all captured traffic — game packets waited 0.4-1.9 s. Placeholder lists are dropped, small
    exclude lists inlined. Returns the new argv and the files it no longer watches with their entries: when one
    changes, winws must be restarted (stale())."""
    if Path(argv[0]).name.lower() != "winws.exe":
        return argv, {}
    out, snap, prof = [], {}, []
    def flush():
        for a in prof:
            opt, _, val = a.partition("=")
            f = Path(val)
            if opt not in ("--ipset", "--hostlist", "--ipset-exclude", "--hostlist-exclude") or f.name in LIVE:
                out.append(a)
                continue
            items = _entries(f)
            if items is None:
                out.append(a)
                continue
            real = [e for e in items if e not in PLACEHOLDERS]
            if opt in ("--ipset", "--hostlist"):  # an include list: dropping the only one would match everything
                same = [x for x in prof if x.startswith(opt + "=") and x != a]
                if real or not same:
                    out.append(a)
                    continue
            elif real and len(real) <= INLINE_MAX:
                out.append(("--ipset-exclude-ip=" if opt == "--ipset-exclude" else "--hostlist-exclude-domains=")
                           + ",".join(real))
            elif real:
                out.append(a)
                continue
            snap[f] = items
        prof.clear()
    out.append(argv[0])
    for a in argv[1:]:
        if a == "--new":
            flush()
            out.append(a)
        else:
            prof.append(a)
    flush()
    return out, snap
def stale(snap: dict[Path, list[str] | None]) -> bool:
    """A list file slim() took off winws changed since the start."""
    return any(_entries(f) != items for f, items in snap.items())
def uncapture(argv: list[str], ips: list[str] | set[str]) -> list[str]:
    """Leave VPN servers (and switched-off games) out of the WinDivert filter itself. The game ports (UDP 1024-65535) also matched a VPN's
    own tunnel (WireGuard/AmneziaWG), so every VPN packet went through winws in user mode: ~18% of a core and ping
    spikes in a game next to it. ipset-exclude only skips the desync; the packets were still captured. A game that
    works without the bypass is better off outside winws too: winws now and then holds every captured packet for
    0.4-1.9 s (seen in Valorant: ping spikes only with zapret on)."""
    if not ips or Path(argv[0]).name.lower() != "winws.exe":  # winws2 has its own filter options
        return argv
    f = paths.DATA / "logs" / "winws-filter.txt"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.unlink(missing_ok=True)
    try:  # winws writes the filter it would build from these options and exits (no driver, ~0.1 s)
        subprocess.run(argv + [f"--wf-save={f}"], cwd=Path(argv[0]).parent, capture_output=True, timeout=10,
                       creationflags=CREATE_NO_WINDOW)
        base = f.read_text(encoding="utf-8").strip()
    except (OSError, subprocess.TimeoutExpired):
        return argv
    # WinDivert has no negation of a group ("not (...)" is a parse error): spell it with "!=", IPv6 passes as is
    import ipaddress
    good = []
    for i in ips:  # one CIDR/IPv6 entry made the whole filter fail to compile: the exclusions were lost
        with contextlib.suppress(ValueError):
            good.append(str(ipaddress.IPv4Address(i)))
    vpn_ = " and ".join(f"ip.SrcAddr != {i} and ip.DstAddr != {i}" for i in good[:64])
    text = f"({base})\nand (ipv6 or ({vpn_}))\n"
    if not base or not compiles(text):  # a filter WinDivert rejects would leave the bypass down
        return argv
    f.write_text(text, encoding="utf-8")
    return [a for a in argv if not a.startswith(("--wf-tcp=", "--wf-udp=", "--wf-raw-part="))] + [f"--wf-raw=@{f}"]
_dll_lock = threading.Lock()
def windivert_dll(**kw):
    """Load WinDivert from a content-named copy in DATA, never from vendor/bin: a DLL loaded into
    our process can't be unloaded safely and would block the zapret updater renaming vendor/."""
    src = paths.BIN / "WinDivert.dll"
    body = src.read_bytes()
    root = paths.DATA / "windivert"
    d = root / hashlib.sha256(body).hexdigest()[:12]
    dll = d / "WinDivert.dll"
    with _dll_lock:
        if not dll.exists():
            d.mkdir(parents=True, exist_ok=True)
            drv = paths.BIN / "WinDivert64.sys"  # the DLL installs the driver from its own folder
            if drv.exists():
                shutil.copy2(drv, d / drv.name)
            tmp = d / "WinDivert.dll.tmp"
            tmp.write_bytes(body)
            os.replace(tmp, dll)  # the DLL appears last: its presence means the copy is complete
            for old in root.iterdir():  # copies of older releases (fails harmlessly while one is loaded)
                if old != d:
                    shutil.rmtree(old, ignore_errors=True)
    return ctypes.WinDLL(str(dll), **kw)
def compiles(flt: str) -> bool:
    try:
        f = windivert_dll().WinDivertHelperCompileFilter
        f.argtypes = [ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p]
        return bool(f(flt.encode(), 0, None, 0, None, None))  # layer 0: NETWORK, as winws opens it
    except (OSError, AttributeError):
        return False
def output_file() -> Path:
    """What winws printed in its last run (errors such as a WinDivert driver that won't load)."""
    return paths.DATA / "logs" / "winws.log"
def last_output(lines: int = 20) -> str:
    try:
        text = output_file().read_bytes().decode("utf-8", errors="replace")
    except OSError:
        return ""
    return "\n".join(text.splitlines()[-lines:])
def explain(output: str) -> str | None:
    """A human cause from winws' own words, or None."""
    import re
    low = output.lower()
    if "already running with the same filter" in low:
        return "уже запущен другой обход zapret (служба или .bat) — закройте его или «Диагностика» → «Исправить»"
    m = re.search(r"error opening filter: (.*)", output, re.I)
    if m:
        why = m.group(1).strip()
        code = re.search(r"\b(\d{1,5})\b", why)
        c = int(code.group(1)) if code else None
        if c == 577 or "hash" in why.lower() or "signature" in why.lower():
            return ("Windows не загрузила драйвер WinDivert (подпись не принята). Чаще всего мешает антивирус или "
                    "«Изоляция ядра → Целостность памяти»")
        if c == 5 or "denied" in why.lower() or "отказано" in why.lower():
            return "нет прав администратора для драйвера WinDivert — запустите ZapretAuto от администратора"
        if c == 2 or "not find" in why.lower() or "не удается найти" in why.lower():
            return "нет файла драйвера WinDivert — скорее всего, его удалил антивирус; добавьте папку в исключения"
        if c in (1058, 1275) or "blocked" in why.lower() or "заблокирован" in why.lower():
            return "драйвер WinDivert заблокирован системой или антивирусом"
        return f"драйвер WinDivert не запустился: {why[:120]}"
    m = re.search(r"cannot access (?:hostlist|ipset) file '([^']+)'", output)
    if m:
        return f"обход не может прочитать список {Path(m.group(1)).name} — проверьте антивирус"
    return None
