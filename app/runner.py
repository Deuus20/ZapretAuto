"""Start/stop winws.exe (single instance)."""
import ctypes
import subprocess

from . import paths
from .strategies import Strategy

CREATE_NO_WINDOW = 0x08000000


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except OSError:
        return False


class Runner:
    def __init__(self) -> None:
        self.proc: subprocess.Popen | None = None
        self.current: Strategy | None = None

    @property
    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def start(self, s: Strategy, game_tcp: bool = False, game_udp: bool = False) -> None:
        self.stop()
        paths.sync_lists()
        self.proc = subprocess.Popen(
            s.argv(game_tcp, game_udp), cwd=paths.BIN, creationflags=CREATE_NO_WINDOW,
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        )
        self.current = s

    def stop(self) -> None:
        if self.running:
            self.proc.terminate()
            try:
                self.proc.wait(5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        # also kill instances started by upstream .bat files
        subprocess.run(["taskkill", "/F", "/IM", "winws.exe"], capture_output=True, creationflags=CREATE_NO_WINDOW)
        self.proc = self.current = None
