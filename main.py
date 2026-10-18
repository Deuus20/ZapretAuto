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
        args = " ".join(f'"{a}"' for a in sys.argv[1:])
        if getattr(sys, "frozen", False):
            params = args
        else:
            params = f'"{Path(__file__).resolve()}" {args}'
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
    server.setSocketOptions(QLocalServer.UserAccessOption)  # only this user may poke the instance
    server.listen("ZapretAuto")
    try:
        from app import updater
        updater.recover_vendor()  # finish/undo an interrupted zapret update
    except Exception:  # noqa: BLE001
        pass
    from app import logs
    logs.setup()
    from app.gui.window import MainWindow
    w = MainWindow()
    server.newConnection.connect(lambda: (server.nextPendingConnection().disconnectFromServer(), w.show_window()))
    if "--tray" not in sys.argv:  # autostart with Windows starts quietly in the tray
        w.show_window()
    w.startup(autostart=not os.environ.get("ZAPRET_NO_AUTOSTART"))
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
