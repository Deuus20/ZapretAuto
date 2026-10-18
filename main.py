import ctypes
import os
import sys
from pathlib import Path

ROOT = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))


def main() -> int:
    from app.runner import is_admin
    if not is_admin():
        if getattr(sys, "frozen", False):
            params = ""
        else:
            params = f'"{Path(__file__).resolve()}"'
        ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, params, str(ROOT), 1)
        return 0
    from PySide6.QtNetwork import QLocalServer, QLocalSocket
    from PySide6.QtWidgets import QApplication
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    sock = QLocalSocket()
    sock.connectToServer("ZapretAuto")
    if sock.waitForConnected(300):
        sock.write(b"show")
        sock.waitForBytesWritten(300)
        return 0
    QLocalServer.removeServer("ZapretAuto")
    server = QLocalServer()
    server.listen("ZapretAuto")
    from app.gui.window import MainWindow
    w = MainWindow()
    server.newConnection.connect(lambda: (server.nextPendingConnection(), w.show_window()))
    w.show_window()
    w.startup(autostart=not os.environ.get("ZAPRET_NO_AUTOSTART"))
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
