"""Background work for the GUI: QThread runner and cross-thread signals."""
from PySide6.QtCore import QObject, QThread, Signal


class Worker(QThread):
    """Run fn(log) off the UI thread; result via `done`."""
    line = Signal(str)
    done = Signal(object)

    def __init__(self, fn):
        super().__init__()
        self.fn = fn

    def run(self):
        try:
            self.done.emit(self.fn(self.line.emit))
        except Exception as e:  # noqa: BLE001
            self.line.emit(f"ошибка: {e}")
            self.done.emit(None)


class Bridge(QObject):
    log = Signal(str)
    games_changed = Signal()
    update_checked = Signal(object, bool)
    app_checked = Signal(object, bool)
    diag_done = Signal(object)
