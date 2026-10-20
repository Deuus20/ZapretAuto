"""Start/stop winws.exe (single instance)."""
import ctypes
import subprocess
import threading
from pathlib import Path

import psutil

from . import paths, vpn
from .strategies import Strategy

EXES = ("winws.exe", "winws2.exe")  # winws2: experimental zapret2 mode

CREATE_NO_WINDOW = 0x08000000


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

    @property
    def running(self) -> bool:
        p = self.proc
        return p is not None and p.poll() is None

    def start(self, s: Strategy, game_tcp: bool = False, game_udp: bool = False,
              auto_hostlist: bool = False) -> None:
        with self._lock:
            self.stop()
            paths.sync_lists()
            try:
                vpn.write_exclude()
            except Exception:  # noqa: BLE001
                pass
            argv = s.argv(game_tcp, game_udp, auto_hostlist)
            log = output_file()
            log.parent.mkdir(parents=True, exist_ok=True)
            self._out = open(log, "wb")  # a file, not a PIPE: an undrained pipe would block winws
            self.proc = subprocess.Popen(
                argv, cwd=Path(argv[0]).parent, creationflags=CREATE_NO_WINDOW,
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
                    try:
                        subprocess.run(["taskkill", "/F", "/IM", exe], capture_output=True,
                                       creationflags=CREATE_NO_WINDOW, timeout=10)
                    except subprocess.TimeoutExpired:
                        pass
            self.proc = self.current = None
            if getattr(self, "_out", None):
                self._out.close()
                self._out = None


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
