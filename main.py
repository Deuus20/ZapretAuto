import ctypes
import os
import sys
from pathlib import Path

ROOT = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))


def selftest() -> int:
    """`ZapretAuto.exe --selftest`: no GUI, no admin; checks the frozen build can load its parts."""
    out = []
    try:
        from app import strategies, tester
        out.append(f"strategies: {len(strategies.load_all())}")
        import importlib.util
        out.append("curl_cffi: ok" if importlib.util.find_spec("curl_cffi") else "curl_cffi: MISSING")
        st, _, n = tester.fetch("https://github.com", 4096, 10)
        out.append(f"fetch github: {st} {n}B")
        code = 0 if all("MISSING" not in x for x in out) else 1
    except Exception as e:  # noqa: BLE001
        out.append(f"error: {e!r}")
        code = 1
    (ROOT / "selftest.txt").write_text("\n".join(out) + "\n", encoding="utf-8")
    return code


def cleanup() -> int:
    """`ZapretAuto.exe --cleanup` (run by the uninstaller, elevated): take our block out of the hosts file."""
    try:
        from app import diag
        diag._hosts_remove()
        return 0
    except Exception:  # noqa: BLE001 (no hosts / no block: nothing to undo)
        return 1


def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()
    if "--cleanup" in sys.argv:
        return cleanup()
    from app.runner import is_admin
    if not is_admin():
        args = " ".join(f'"{a}"' for a in sys.argv[1:])
        if getattr(sys, "frozen", False):
            params = args
        else:
            params = f'"{Path(__file__).resolve()}" {args}'
        ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, params, str(ROOT), 1)
        return 0
    try:  # own taskbar identity: Windows then shows our window icon instead of a cached exe icon
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("ZapretAuto.App")
    except (AttributeError, OSError):
        pass
    from PySide6.QtNetwork import QLocalServer, QLocalSocket
    from PySide6.QtWidgets import QApplication
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    from PySide6.QtCore import QTranslator
    tr = QTranslator(app)
    if tr.load(str(ROOT / "assets" / "qtbase_ru.qm")):  # «Да»/«Нет» instead of Yes/No in dialogs
        app.installTranslator(tr)
    sock = QLocalSocket()
    sock.connectToServer("ZapretAuto")
    if sock.waitForConnected(2000):  # 300 ms missed a busy first instance: two trays killed each other's winws
        sock.write(b"show")
        sock.waitForBytesWritten(300)
        return 0
    QLocalServer.removeServer("ZapretAuto")
    server = QLocalServer()
    server.setSocketOptions(QLocalServer.UserAccessOption)  # only this user may poke the instance
    if not server.listen("ZapretAuto"):  # the name is taken: another instance answered late
        sock.connectToServer("ZapretAuto")
        if sock.waitForConnected(2000):
            sock.write(b"show")
            sock.waitForBytesWritten(300)
        return 0
    try:
        from app import updater
        updater.recover_vendor()  # finish/undo an interrupted zapret update
    except Exception:  # noqa: BLE001
        pass
    from app import logs, settings
    logs.setup()
    settings.first_run()  # fresh install: games watched, autostart on, full settings shown
    from app.gui.window import MainWindow
    w = MainWindow()
    server.newConnection.connect(lambda: (server.nextPendingConnection().disconnectFromServer(), w.show_window()))
    if "--tray" not in sys.argv:  # autostart with Windows starts quietly in the tray
        w.show_window()
    w.startup(autostart=not os.environ.get("ZAPRET_NO_AUTOSTART"))
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
