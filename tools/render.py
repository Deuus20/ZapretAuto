"""Render the main window offscreen to PNGs (one per tab), without touching the screen or the bypass.

Usage: python tools/render.py <out_dir> [width height]
Uses a temp data dir and ZAPRET_NO_AUTOSTART, so the real settings and winws are not affected.
"""
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"))  # offscreen has no fonts
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import paths, settings  # noqa: E402

paths.DATA = Path(tempfile.mkdtemp(prefix="za_render_"))
paths.LISTS = paths.DATA / "lists"
settings._cache = None
settings.save_settings(strategy={"base": "ALT8", "picks": {}}, show_advanced=True, game_watch=True,
                       ipset_updated=9e9)

from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication(sys.argv[:1])
from PySide6.QtCore import QTranslator  # noqa: E402

_tr = QTranslator(app)
if _tr.load(str(Path(__file__).resolve().parent.parent / "assets" / "qtbase_ru.qm")):
    app.installTranslator(_tr)
from app.gui.window import MainWindow  # noqa: E402

out = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
out.mkdir(parents=True, exist_ok=True)
w = MainWindow()
if len(sys.argv) > 3:
    w.resize(int(sys.argv[2]), int(sys.argv[3]))
w.set_chips({"Discord": True, "YouTube": True, "Голос Discord": False})
w.lbl_isp.setText("Провайдер: PJSC Moscow city telephone network · в общей базе отчётов: 4, у вас та же комбинация, "
                  "что у большинства")
w.lbl_isp.show()
w.show()
for i in range(w.tabs.count()):
    w.tabs.setCurrentIndex(i)
    app.processEvents()
    app.processEvents()
    w.grab().save(str(out / f"tab{i}.png"))
w.tray.hide()
print(out)

# extra states: games with data, busy pick, an error line, a message box
from app import gamewatch as _gw  # noqa: E402

_gw.GameWatcher.start = lambda self: None
w.start_watcher()
w.watcher._games.update({
    "wardogsclient-win64-shipping.exe": {"path": "x", "ips": set(f"9.9.9.{i}" for i in range(110)), "active": True,
                                         "ping_ms": 38.0},
    "deadlock.exe": {"path": "x", "ips": set(f"8.8.8.{i}" for i in range(56)), "active": False, "ping_ms": None}})
w.tabs.setCurrentIndex(1)
w.fill_games()
w.table.selectRow(0)
app.processEvents()
w.grab().save(str(out / "games_rows.png"))
w.tabs.setCurrentIndex(0)
w.busy, w.busy_text, w._cancellable = True, "Подбираю стратегию…", True
w.refresh()
w.substatus.setText("Проверено стратегий: 7 из 21, осталось около 2 мин")
w.progress.setRange(0, 25)
w.progress.setValue(7)
app.processEvents()
w.grab().save(str(out / "busy.png"))
w.busy, w.error = False, "Не удалось запустить обход. Откройте «Диагностика» → «Найти проблемы»."
w.refresh()
app.processEvents()
w.grab().save(str(out / "error.png"))
from PySide6.QtWidgets import QMessageBox  # noqa: E402

mb = QMessageBox(QMessageBox.Question, "ZapretAuto", "Готово! Запускать ZapretAuto вместе с Windows?",
                 QMessageBox.Yes | QMessageBox.No, w)
mb.show()
app.processEvents()
mb.grab().save(str(out / "msgbox.png"))
mb.close()
w.stop_watcher()
w.error = ""
w.runner.proc = type("P", (), {"poll": lambda self: None})()  # looks running
w.set_chips({"Discord": True, "YouTube": True, "Голос Discord": True})
for t in ("запущено: ALT8", "проверка доступа: Discord ✓   YouTube ✓   Голос Discord ✓", "найдена игра: Deadlock"):
    w.log(t)
w.refresh()
app.processEvents()
w.grab().save(str(out / "main_on.png"))
w.runner.proc = None
w.on_diag_done([
    {"label": "Base Filtering Engine", "level": "ok", "detail": "Running"},
    {"label": "Файлы обхода", "level": "ok", "detail": "winws.exe, WinDivert на месте"},
    {"label": "Подмена DNS", "level": "err", "detail": "провайдер подменяет адреса: youtube.com — обход не поможет "
     "без защищённого DNS. «Исправить» включит его в Chrome, Edge и Яндекс.Браузере", "fix": "browser_doh"},
    {"label": "QUIC в браузерах", "level": "warn", "detail": "QUIC до YouTube не проходит — «Исправить» отключит QUIC",
     "fix": "disable_quic"},
    {"label": "Кэш Discord", "level": "ok", "detail": "если Discord висит на загрузке — «Исправить» очистит кэш",
     "fix": "discord_cache"}])
w.tabs.setCurrentIndex(3)
app.processEvents()
app.processEvents()
w.grab().save(str(out / "diag_rows.png"))
w.tabs.setCurrentIndex(2)
w.ports["udp"].setText("99999")
w.on_ports("udp")
area = w.tabs.currentWidget().findChild(__import__("PySide6.QtWidgets", fromlist=["QScrollArea"]).QScrollArea)
area.verticalScrollBar().setValue(area.verticalScrollBar().maximum())
app.processEvents()
w.grab().save(str(out / "settings_bottom.png"))
# light theme (Windows «light» apps mode)
from app.gui import style as _st  # noqa: E402

_st.windows_light = lambda: True
w.apply_theme()
w.runner.proc = type("P", (), {"poll": lambda self: None})()
w.set_chips({"Discord": True, "YouTube": True, "Голос Discord": False})
w.lbl_isp.setText("Провайдер: PJSC Moscow city telephone network · в общей базе отчётов: 4, у вас та же комбинация, "
                  "что у большинства")
w.lbl_isp.show()
w.refresh()
for i, name in ((0, "light_home"), (2, "light_settings"), (3, "light_diag")):
    w.tabs.setCurrentIndex(i)
    app.processEvents()
    app.processEvents()
    w.grab().save(str(out / f"{name}.png"))
w.runner.proc = None
