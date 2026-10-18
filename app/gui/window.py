"""Tray icon + main window."""
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QThread, Signal
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QHBoxLayout, QInputDialog, QLabel,
                               QMenu, QMessageBox, QPlainTextEdit, QPushButton, QSystemTrayIcon,
                               QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget)

from app import strategies, tester
from app.runner import Runner

try:
    from app import updater
except Exception:  # noqa: BLE001
    updater = None
try:
    from app import gamewatch
except Exception:  # noqa: BLE001
    gamewatch = None

ROOT = Path(__file__).resolve().parents[2]
TASK = "ZapretAuto"
AUTO = "Авто (микс)"
COLORS = {"on": "#2ecc71", "off": "#8a8a8a", "busy": "#f39c12"}


def circle(color: str) -> QIcon:
    pm = QPixmap(32, 32)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(QColor(color))
    p.setPen(QColor("#222"))
    p.drawEllipse(3, 3, 26, 26)
    p.end()
    return QIcon(pm)


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


class MainWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("ZapretAuto")
        self.resize(640, 520)
        self.runner = Runner()
        self.watcher = None
        self.worker: Worker | None = None
        self.busy = False
        self._auto_check = False
        self.bridge = Bridge()
        self.bridge.log.connect(self.log)
        self.bridge.games_changed.connect(self.on_games_changed)
        self.bridge.update_checked.connect(self.on_update_checked)
        self.settings = tester.load_settings()
        self._build()
        self._tray()
        self.refresh()

    # ---------- UI ----------
    def _build(self):
        lay = QVBoxLayout(self)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setStyleSheet("font-size:14px;font-weight:bold")
        lay.addWidget(self.status)
        row = QHBoxLayout()
        self.btn_toggle = QPushButton("Старт")
        self.btn_toggle.clicked.connect(self.toggle)
        self.btn_auto = QPushButton("Автоподбор")
        self.btn_auto.clicked.connect(self.autopick)
        row.addWidget(self.btn_toggle)
        row.addWidget(self.btn_auto)
        lay.addLayout(row)
        row = QHBoxLayout()
        row.addWidget(QLabel("Стратегия:"))
        self.combo = QComboBox()
        self.combo.addItem(AUTO)
        self.combo.addItems([s.name for s in strategies.load_all()])
        self.combo.activated.connect(self.on_combo)
        row.addWidget(self.combo, 1)
        lay.addLayout(row)
        s = self.settings
        self.cb = {}
        for key, text, default in [("game_tcp", "Game filter TCP", False), ("game_udp", "Game filter UDP", False),
                                   ("auto_update", "Автообновление", True), ("autostart", "Автозапуск с Windows", False),
                                   ("game_watch", "Следить за играми", False)]:
            c = QCheckBox(text)
            c.setChecked(bool(s.get(key, default)))
            c.toggled.connect(lambda v, k=key: self.on_check(k, v))
            self.cb[key] = c
            lay.addWidget(c)
        self.tabs = QTabWidget()
        lay.addWidget(self.tabs, 1)
        self.logbox = QPlainTextEdit(readOnly=True)
        self.tabs.addTab(self.logbox, "Лог")
        g = QWidget()
        gl = QVBoxLayout(g)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["exe", "IP", "ping", "активна"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        gl.addWidget(self.table)
        r = QHBoxLayout()
        b1 = QPushButton("Игнорировать")
        b1.clicked.connect(self.ignore_game)
        b2 = QPushButton("Добавить вручную exe")
        b2.clicked.connect(self.add_game)
        r.addWidget(b1)
        r.addWidget(b2)
        gl.addLayout(r)
        self.tabs.addTab(g, "Игры")
        u = QWidget()
        ul = QVBoxLayout(u)
        self.lbl_ver = QLabel()
        ul.addWidget(self.lbl_ver)
        r = QHBoxLayout()
        self.btn_check = QPushButton("Проверить")
        self.btn_check.clicked.connect(self.check_update)
        self.btn_update = QPushButton("Обновить")
        self.btn_update.clicked.connect(lambda: self.do_update(False))
        r.addWidget(self.btn_check)
        r.addWidget(self.btn_update)
        ul.addLayout(r)
        ul.addStretch()
        self.tabs.addTab(u, "Обновления")
        ver = "недоступно"
        if updater:
            try:
                ver = updater.current_version()
            except Exception:  # noqa: BLE001
                pass
        self.lbl_ver.setText(f"Версия zapret: {ver}")
        if not updater:
            self.btn_check.setEnabled(False)
            self.btn_update.setEnabled(False)
        if not gamewatch:
            self.cb["game_watch"].setEnabled(False)

    def _tray(self):
        self.tray = QSystemTrayIcon(circle(COLORS["off"]), self)
        m = QMenu()
        self.act_toggle = QAction("Включить", m)
        self.act_toggle.triggered.connect(self.toggle)
        a2 = QAction("Подобрать стратегию", m)
        a2.triggered.connect(self.autopick)
        a3 = QAction("Открыть", m)
        a3.triggered.connect(self.show_window)
        a4 = QAction("Выход", m)
        a4.triggered.connect(self.quit)
        for a in (self.act_toggle, a2, a3, a4):
            m.addAction(a)
        self._menu = m
        self.tray.setContextMenu(m)
        self.tray.activated.connect(lambda r: self.show_window() if r == QSystemTrayIcon.DoubleClick else None)
        self.tray.show()

    # ---------- helpers ----------
    def log(self, text: str):
        self.logbox.appendPlainText(text)

    def show_window(self):
        self.show()
        self.setWindowState(self.windowState() & ~Qt.WindowMinimized)
        self.raise_()
        self.activateWindow()

    def closeEvent(self, e):
        e.ignore()
        self.hide()

    def describe(self) -> str:
        c = self.settings.get("strategy")
        if not c:
            return "не выбрана"
        if c.get("picks"):
            return f"{c['base']} + микс ({', '.join(sorted(set(c['picks'].values())))})"
        return c["base"]

    def refresh(self):
        run = self.runner.running
        state = "busy" if self.busy else ("on" if run else "off")
        self.tray.setIcon(circle(COLORS[state]))
        self.setWindowIcon(circle(COLORS[state]))
        self.status.setText(f"{'Работает' if run else 'Остановлено'}{' (подбор…)' if self.busy else ''} | Стратегия: {self.describe()}")
        self.btn_toggle.setText("Стоп" if run else "Старт")
        self.act_toggle.setText("Выключить" if run else "Включить")
        self.btn_auto.setEnabled(not self.busy)
        c = self.settings.get("strategy")
        self.combo.setCurrentIndex(0 if not c or c.get("picks") else max(0, self.combo.findText(c["base"])))

    def save(self, **kw):
        tester.save_settings(**kw)
        self.settings = tester.load_settings()

    # ---------- runner ----------
    def start_runner(self):
        s = tester.chosen_strategy()
        if not s:
            self.log("стратегия не выбрана")
            return False
        try:
            self.runner.start(s, self.settings.get("game_tcp", False),
                              self.settings.get("game_udp", False) or self._game_udp_wanted())
            self.log(f"запущено: {s.name}")
        except Exception as e:  # noqa: BLE001
            self.log(f"ошибка запуска: {e}")
        self.refresh()
        return self.runner.running

    def _game_udp_wanted(self) -> bool:
        return bool(self.watcher and any(g.get("ips") for g in self.watcher.games().values()))

    def toggle(self):
        if self.runner.running:
            self.runner.stop()
            self.log("остановлено")
            self.refresh()
        elif not self.start_runner() and not self.settings.get("strategy"):
            self.ask_autopick()

    def on_combo(self, i):
        if i == 0:
            if not self.settings.get("strategy", {}).get("picks"):
                self.log("Авто(микс): запустите автоподбор")
            return
        self.save(strategy={"base": self.combo.itemText(i), "picks": {}})
        if self.runner.running:
            self.start_runner()
        self.refresh()

    def on_check(self, key, v):
        self.save(**{key: v})
        if key == "autostart":
            self.set_autostart(v)
        elif key == "game_watch":
            self.start_watcher() if v else self.stop_watcher()
        elif key in ("game_tcp", "game_udp") and self.runner.running:
            self.start_runner()

    def set_autostart(self, on: bool):
        if on:
            tr = f'"{sys.executable}"' if getattr(sys, "frozen", False) else f'"{sys.executable}" "{ROOT / "main.py"}"'
            cmd = ["schtasks", "/Create", "/F", "/TN", TASK, "/TR", tr, "/RL", "HIGHEST", "/SC", "ONLOGON"]
        else:
            cmd = ["schtasks", "/Delete", "/F", "/TN", TASK]
        r = subprocess.run(cmd, capture_output=True, text=True, creationflags=0x08000000)
        self.log(f"автозапуск {'вкл' if on else 'выкл'}: " + ("ок" if r.returncode == 0 else (r.stderr or r.stdout).strip()))

    # ---------- autopick ----------
    def ask_autopick(self):
        if QMessageBox.question(self, "ZapretAuto", "Стратегия не выбрана. Запустить автоподбор?") == QMessageBox.Yes:
            self.autopick()

    def autopick(self, *_):
        if self.busy:
            return
        vpn = tester.vpn_active()
        if vpn:
            QMessageBox.warning(self, "VPN", f"Активен VPN ({vpn}). Отключите его: результаты подбора были бы неверны.")
            self.log(f"автоподбор отменён: VPN {vpn}")
            return
        self.runner.stop()
        self.busy = True
        self.refresh()
        self.tabs.setCurrentIndex(0)
        self.worker = Worker(lambda log: tester.auto_pick(log))
        self.worker.line.connect(self.log)
        self.worker.done.connect(self._autopick_done)
        self.worker.start()

    def _autopick_done(self, res):
        self.busy = False
        self.settings = tester.load_settings()
        self.log("автоподбор завершён" if res else "автоподбор не дал результата")
        if res:
            self.start_runner()
        self.refresh()

    # ---------- games ----------
    def start_watcher(self):
        if not gamewatch or self.watcher:
            return
        self.watcher = gamewatch.GameWatcher(lambda *a: self.bridge.games_changed.emit(), self.bridge.log.emit)
        self.watcher.start()
        self.log("слежение за играми включено")

    def stop_watcher(self):
        if self.watcher:
            self.watcher.stop()
            self.watcher = None
            self.log("слежение за играми выключено")

    def on_games_changed(self):
        self.fill_games()
        if self.runner.running:
            self.start_runner()  # restart with game filters

    def fill_games(self):
        games = self.watcher.games() if self.watcher else {}
        ign = set(self.settings.get("games_ignore", []))
        rows = [(k, v) for k, v in games.items() if k not in ign]
        self.table.setRowCount(len(rows))
        for i, (exe, g) in enumerate(rows):
            ping = g.get("ping_ms")
            for j, t in enumerate([exe, str(len(g.get("ips", ()))), "-" if ping is None else f"{ping:.0f}",
                                   "да" if g.get("active") else "нет"]):
                self.table.setItem(i, j, QTableWidgetItem(t))

    def ignore_game(self):
        r = self.table.currentRow()
        if r < 0:
            return
        exe = self.table.item(r, 0).text()
        self.save(games_ignore=sorted(set(self.settings.get("games_ignore", [])) | {exe}))
        self.fill_games()

    def add_game(self):
        exe, ok = QInputDialog.getText(self, "Игра", "Имя exe (например game.exe):")
        if ok and exe.strip():
            exe = exe.strip()
            self.save(games_force=sorted(set(self.settings.get("games_force", [])) | {exe}))
            if self.watcher and hasattr(self.watcher, "add_manual"):
                self.watcher.add_manual(exe)
            self.log(f"игра добавлена: {exe}")

    # ---------- updates ----------
    def check_update(self):
        if updater:
            self.log("проверка обновлений…")
            updater.check_async(lambda tag, newer: self.bridge.update_checked.emit(tag, newer))

    def on_update_checked(self, tag, newer):
        self.log(f"последняя версия: {tag}" + (" (есть обновление)" if newer else " (актуально)" if tag else " (не удалось проверить)"))
        if self._auto_check:
            self._auto_check = False
            if newer:
                self.do_update(True)

    def do_update(self, auto: bool):
        if not updater or self.busy:
            return
        was = self.runner.running
        self.busy = True
        self.refresh()
        self.worker = Worker(lambda log: updater.update(log, self.runner.stop))
        self.worker.line.connect(self.log)
        self.worker.done.connect(lambda ok: self._update_done(ok, was, auto))
        self.worker.start()

    def _update_done(self, ok, was, auto):
        self.busy = False
        self.log("обновление завершено" if ok else "обновление не выполнено")
        if ok:
            if auto:
                self.autopick()
                return
            if was:
                self.start_runner()
            if QMessageBox.question(self, "ZapretAuto", "Обновлено. Запустить автоподбор стратегии?") == QMessageBox.Yes:
                self.autopick()
        elif was:
            self.start_runner()
        self.refresh()

    # ---------- lifecycle ----------
    def startup(self, autostart=True):
        if not autostart:
            self.log("ZAPRET_NO_AUTOSTART: автозапуск отключён")
            return
        if self.settings.get("auto_update", True) and updater:
            self._auto_check = True
            self.check_update()
        if tester.chosen_strategy():
            self.start_runner()
        else:
            self.ask_autopick()
        if self.settings.get("game_watch"):
            self.start_watcher()

    def quit(self):
        self.runner.stop()
        self.stop_watcher()
        self.tray.hide()
        QApplication.quit()
