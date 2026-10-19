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
            self.proc = subprocess.Popen(
                argv, cwd=Path(argv[0]).parent, creationflags=CREATE_NO_WINDOW,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,  # an undrained PIPE would block winws
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
