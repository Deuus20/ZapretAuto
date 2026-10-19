"""Background work for the GUI: a worker on a Python thread, and cross-thread signals."""
import threading

from PySide6.QtCore import QObject, Signal


class Worker(QObject):
    """Run fn(log) off the UI thread; progress via `line`, result via `done`, then `finished`.

    A plain Python thread, not a QThread: subprocess calls made from a QThread (a thread Python did not
    create) leaked one OS handle each — the once-a-minute VPN refresh grew the app by ~60 handles/hour.
    Signals emitted from this thread are delivered to the GUI thread via Qt's queued connections.
    """
    line = Signal(str)
    done = Signal(object)
    finished = Signal()

    def __init__(self, fn):
        super().__init__()
        self.fn = fn
        self._t: threading.Thread | None = None

    def _main(self):
        try:
            self.done.emit(self.fn(self.line.emit))
        except Exception as e:  # noqa: BLE001
            self.line.emit(f"ошибка: {e}")
            self.done.emit(None)
        finally:
            self.finished.emit()

    def start(self):
        self._t = threading.Thread(target=self._main, daemon=True, name="ZapretAutoWorker")
        self._t.start()

    def isRunning(self) -> bool:  # noqa: N802 (QThread-compatible name)
        return bool(self._t and self._t.is_alive())

    def wait(self, ms: int | None = None) -> bool:
        if self._t:
            self._t.join(None if ms is None else ms / 1000)
        return not self.isRunning()


class Bridge(QObject):
    log = Signal(str)
    games_changed = Signal()
    update_checked = Signal(object, bool)
    app_checked = Signal(object, bool)
    diag_done = Signal(object)
