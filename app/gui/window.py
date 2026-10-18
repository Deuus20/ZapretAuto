"""Tray icon + main window."""
import re
import subprocess
import sys
import time
from pathlib import Path

from PySide6.QtCore import QObject, QSize, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QFileDialog, QFrame, QGridLayout, QGroupBox, QHBoxLayout, QInputDialog, QLabel,
                               QMenu, QMessageBox, QPlainTextEdit, QPushButton, QSystemTrayIcon,
                               QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget)

from app import diag, lists, strategies, tester
from app.runner import Runner

try:
    from app import updater
except Exception:  # noqa: BLE001
    updater = None
try:
    from app import gamewatch
except Exception:  # noqa: BLE001
    gamewatch = None

from app.paths import ROOT
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


def power_icon(color: str, size: int = 44) -> QIcon:
    """Classic power symbol: open arc + vertical bar."""
    from PySide6.QtGui import QPen
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    pen = QPen(QColor(color), size * 0.1, Qt.SolidLine, Qt.RoundCap)
    p.setPen(pen)
    m = size * 0.16
    p.drawArc(int(m), int(m), int(size - 2 * m), int(size - 2 * m), 120 * 16, 300 * 16)
    p.drawLine(int(size / 2), int(size * 0.08), int(size / 2), int(size * 0.45))
    p.end()
    return QIcon(pm)


STYLE = """
QWidget { background: #15171c; color: #e6e8ee; font-family: "Segoe UI"; font-size: 10pt; }
QFrame#card { background: #1e2129; border-radius: 14px; }
QLabel#title { font-size: 17pt; font-weight: 600; background: transparent; }
QLabel#sub { color: #b4bccb; background: transparent; }
QPushButton { background: #262a34; border: 1px solid #323744; border-radius: 8px; padding: 7px 14px; }
QPushButton:hover { background: #2e3340; }
QPushButton:disabled { color: #8b93a3; }
QPushButton:focus, QCheckBox:focus, QComboBox:focus { outline: none; border: 1px solid #3b82f6; }
QPushButton#link { background: transparent; border: none; color: #60a5fa; padding: 2px 0; }
QPushButton#link:hover { color: #93c5fd; }
QPushButton#primary { background: #3b82f6; border: none; color: white; font-weight: 600; }
QPushButton#primary:hover { background: #4b8ff8; }
QPushButton#primary:disabled { background: #2a3550; color: #8090b0; }
QPushButton#power { border-radius: 48px; font-size: 34pt; border: 3px solid #4b5263; background: #262a34; color: #9aa3b5; }
QPushButton#power[state="on"] { border-color: #22c55e; color: #22c55e; background: #16261d; }
QPushButton#power[state="busy"] { border-color: #f59e0b; color: #f59e0b; background: #2a2314; }
QGroupBox { border: 1px solid #2a2f3a; border-radius: 10px; margin-top: 10px; padding: 12px 10px 8px 10px; }
QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 4px; color: #9aa3b5; }
QCheckBox { spacing: 8px; background: transparent; padding: 3px 0; }
QCheckBox::indicator { width: 16px; height: 16px; border: 2px solid #4b5263; border-radius: 5px; background: #1b1e25; }
QCheckBox::indicator:hover { border-color: #3b82f6; }
QCheckBox::indicator:checked { background: #3b82f6; border-color: #3b82f6; }
QComboBox { background: #262a34; border: 1px solid #323744; border-radius: 8px; padding: 6px 10px; }
QComboBox::drop-down { border: none; width: 26px; }
QComboBox QAbstractItemView { background: #1e2129; selection-background-color: #3b82f6; }
QTabWidget::pane { border: 1px solid #2a2f3a; border-radius: 8px; top: -1px; }
QTabBar::tab { background: transparent; padding: 7px 14px; color: #9aa3b5; border-bottom: 2px solid transparent; }
QTabBar::tab:selected { color: #e6e8ee; border-bottom: 2px solid #3b82f6; }
QPlainTextEdit, QTableWidget, QListWidget { background: #1b1e25; border: none; border-radius: 8px; font-family: Consolas; }
QHeaderView::section { background: #1e2129; color: #9aa3b5; border: none; padding: 5px; }
QToolTip { background: #262a34; color: #e6e8ee; border: 1px solid #3b82f6; padding: 6px; }
"""


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


class MainWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("ZapretAuto")
        self.resize(700, 680)
        self.runner = Runner()
        self.watcher = None
        self.worker: Worker | None = None
        self.busy = False
        self.busy_text = ""
        self.error = ""
        self._workers: list = []  # keep QThreads alive until finished
        self._expect_running = False
        self._hint_shown = False
        self._auto_check = False
        self.bridge = Bridge()
        self.bridge.log.connect(self.log)
        self.bridge.games_changed.connect(self.on_games_changed)
        self.bridge.update_checked.connect(self.on_update_checked)
        self.bridge.app_checked.connect(self.on_app_checked)
        self.bridge.diag_done.connect(self.on_diag_done)
        self.settings = tester.load_settings()
        self._build()
        self._tray()
        self.refresh()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(2000)
        self._health_fails = 0
        self._health_running = False
        self._net = None
        self._next_health = time.time() + 120

    # ---------- UI ----------
    def _build(self):
        QApplication.instance().setStyle("Fusion")
        QApplication.instance().setStyleSheet(STYLE)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.setSpacing(12)

        # header: big power button + status
        head = QFrame(objectName="card")
        hl = QHBoxLayout(head)
        hl.setContentsMargins(16, 16, 16, 16)
        hl.setSpacing(18)
        self.btn_toggle = QPushButton(objectName="power")
        self.btn_toggle.setIconSize(QSize(44, 44))
        self.btn_toggle.setFixedSize(96, 96)
        self.btn_toggle.setCursor(Qt.PointingHandCursor)
        self.btn_toggle.setToolTip("Включить или выключить обход блокировок")
        self.btn_toggle.clicked.connect(self.toggle)
        hl.addWidget(self.btn_toggle, 0, Qt.AlignVCenter)
        col = QVBoxLayout()
        col.setSpacing(4)
        self.status = QLabel(objectName="title")
        self.substatus = QLabel(objectName="sub")
        self.substatus.setWordWrap(True)
        col.addStretch()
        col.addWidget(self.status)
        col.addWidget(self.substatus)
        self.btn_check_access = QPushButton("Проверить доступ")
        self.btn_check_access.setToolTip("Проверить, открываются ли Discord, YouTube и голосовой чат прямо сейчас")
        self.btn_check_access.setCursor(Qt.PointingHandCursor)
        self.btn_check_access.clicked.connect(self.check_access)
        col.addWidget(self.btn_check_access, 0, Qt.AlignLeft)
        col.addStretch()
        hl.addLayout(col, 1)
        lay.addWidget(head)

        # strategy row
        row = QHBoxLayout()
        self.btn_auto = QPushButton("Подобрать лучшую стратегию", objectName="primary")
        self.btn_auto.setCursor(Qt.PointingHandCursor)
        self.btn_auto.setToolTip("Проверить все стратегии на Discord, YouTube и голосовом трафике "
                                 "и собрать лучшую комбинацию. Занимает 3-5 минут.")
        self.btn_auto.clicked.connect(self.autopick)
        row.addWidget(self.btn_auto)
        self.combo = QComboBox()
        self.combo.setToolTip("«Авто» — подобранная комбинация. Можно выбрать стратегию вручную.")
        self.combo.addItem(AUTO)
        self.combo.addItems([s.name for s in strategies.load_all()])
        self.combo.activated.connect(self.on_combo)
        row.addWidget(self.combo, 1)
        lay.addLayout(row)

        # settings
        box = QGroupBox("Настройки")
        grid = QGridLayout(box)
        grid.setHorizontalSpacing(24)
        s = self.settings
        self.cb = {}
        adv = QWidget()
        agrid = QGridLayout(adv)
        agrid.setContentsMargins(0, 0, 0, 0)
        agrid.setHorizontalSpacing(24)
        n_main = n_adv = 0
        for key, text, default, tip, advanced in [
            ("game_watch", "Следить за играми", False,
             "Находить запущенные игры, собирать их серверы и включать для них обход. "
             "Если с обходом соединение стало хуже, IP убирается.", False),
            ("auto_repick", "Сам чинить при сбоях", True,
             "Каждые 10 минут и при смене сети проверять Discord и YouTube. Если они дважды подряд "
             "не открываются — автоматически подобрать стратегию заново.", False),
            ("auto_update", "Автообновление", True,
             "При запуске проверять обновления zapret и ZapretAuto и ставить их автоматически.", False),
            ("autostart", "Запускать вместе с Windows", False,
             "Запускать ZapretAuto при входе в Windows (задача планировщика с правами администратора).", False),
            ("game_udp", "Обход UDP для игр", False,
             "Game filter UDP: обход UDP-трафика игр на портах 1024-65535. Включается сам, когда найдена игра.",
             True),
            ("game_tcp", "Обход TCP для игр", False,
             "Game filter TCP: обход TCP-трафика игр на портах 1024-65535 (только адреса из ipset и списка игр).",
             True),
            ("telegram", "Telegram (экспериментально)", False,
             "Добавить подсети Telegram в обход. Помогает не всем провайдерам: если Telegram "
             "заблокирован по IP, zapret бессилен — нужен прокси.", True),
        ]:
            c = QCheckBox(text)
            c.setToolTip(tip)
            c.setChecked(bool(s.get(key, default)))
            c.toggled.connect(lambda v, k=key: self.on_check(k, v))
            self.cb[key] = c
            if advanced:
                agrid.addWidget(c, n_adv // 2, n_adv % 2)
                n_adv += 1
            else:
                grid.addWidget(c, n_main // 2, n_main % 2)
                n_main += 1
        self.btn_adv = QPushButton(objectName="link")
        self.btn_adv.setCursor(Qt.PointingHandCursor)
        self.btn_adv.clicked.connect(lambda: self.set_advanced(not adv.isVisible()))
        grid.addWidget(self.btn_adv, (n_main + 1) // 2, 0, 1, 2, Qt.AlignLeft)
        grid.addWidget(adv, (n_main + 1) // 2 + 1, 0, 1, 2)
        self.adv_box = adv
        lay.addWidget(box)
        self.box_settings = box
        self.tabs = QTabWidget()
        lay.addWidget(self.tabs, 1)
        self.logbox = QPlainTextEdit(readOnly=True)
        self.tabs.addTab(self.logbox, "Лог")
        g = QWidget()
        gl = QVBoxLayout(g)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Игра", "Адресов", "Пинг, мс", "Запущена"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        gl.addWidget(self.table)
        r = QHBoxLayout()
        b1 = QPushButton("Игнорировать")
        b1.setToolTip("Больше не считать выбранную программу игрой")
        b1.clicked.connect(self.ignore_game)
        b2 = QPushButton("Добавить игру (exe)…")
        b2.setToolTip("Выбрать exe игры вручную, если она не нашлась сама")
        b2.clicked.connect(self.add_game)
        b3 = QPushButton("Сбросить IP игры")
        b3.setToolTip("Убрать все собранные адреса выбранной игры из обхода")
        b3.clicked.connect(self.reset_game)
        r.addWidget(b1)
        r.addWidget(b2)
        r.addWidget(b3)
        gl.addLayout(r)
        self.tabs.addTab(g, "Игры")
        u = QWidget()
        ul = QVBoxLayout(u)
        self.lbl_ver = QLabel()
        ul.addWidget(self.lbl_ver)
        r = QHBoxLayout()
        self.btn_check = QPushButton("Проверить обновления")
        self.btn_check.setToolTip("Проверить, есть ли новые версии zapret и ZapretAuto")
        self.btn_check.clicked.connect(self.check_update)
        self.btn_update = QPushButton("Обновить")
        self.btn_update.setToolTip("Скачать и установить новую версию zapret")
        self.btn_update.clicked.connect(lambda: self.do_update(False))
        r.addWidget(self.btn_check)
        r.addWidget(self.btn_update)
        ul.addLayout(r)
        ul.addStretch()
        self.tabs.addTab(u, "Обновления")
        d = QWidget()
        dl = QVBoxLayout(d)
        self.btn_diag = QPushButton("Найти проблемы")
        self.btn_diag.setToolTip("Найти частые причины, по которым обход не работает")
        self.btn_diag.clicked.connect(self.run_diag)
        dl.addWidget(self.btn_diag)
        self.diag_table = QTableWidget(0, 3)
        self.diag_table.setHorizontalHeaderLabels(["Проверка", "Результат", ""])
        self.diag_table.horizontalHeader().setStretchLastSection(True)
        self.diag_table.setEditTriggers(QTableWidget.NoEditTriggers)
        dl.addWidget(self.diag_table)
        self.tabs.addTab(d, "Диагностика")
        ls = QWidget()
        ll = QVBoxLayout(ls)
        self.list_combo = QComboBox()
        self.list_combo.addItems(list(lists.user_list_paths()))
        self.list_combo.currentIndexChanged.connect(self.load_list)
        ll.addWidget(self.list_combo)
        self.list_edit = QPlainTextEdit()
        ll.addWidget(self.list_edit, 1)
        r = QHBoxLayout()
        b = QPushButton("Сохранить")
        b.clicked.connect(self.save_list)
        self.btn_ipset = QPushButton("Обновить ipset с GitHub")
        self.btn_ipset.setToolTip("Скачать свежий список IP-диапазонов (Cloudflare и др.) для обхода")
        self.btn_ipset.clicked.connect(self.update_ipset)
        r.addWidget(b)
        r.addWidget(self.btn_ipset)
        ll.addLayout(r)
        self.tabs.addTab(ls, "Списки")
        self.tab_lists = self.tabs.indexOf(ls)
        self.load_list()
        self.set_advanced(bool(self.settings.get("show_advanced")), save=False)
        ver = "недоступно"
        if updater:
            try:
                ver = updater.current_version()
            except Exception:  # noqa: BLE001
                pass
        from app import __version__
        self.lbl_ver.setText(f"ZapretAuto {__version__} | zapret {ver}")
        if not updater:
            self.btn_check.setEnabled(False)
            self.btn_update.setEnabled(False)
        if not gamewatch:
            self.cb["game_watch"].setEnabled(False)

    # ---------- diagnostics / lists ----------
    def run_diag(self):
        self.btn_diag.setEnabled(False)
        self.btn_diag.setText("Проверка...")
        self._diag_w = Worker(lambda log: diag.run_checks())
        self._workers.append(self._diag_w)
        self._diag_w.finished.connect(lambda w=self._diag_w: self._workers.remove(w) if w in self._workers else None)
        self._diag_w.done.connect(self.bridge.diag_done.emit)
        self._diag_w.start()

    def on_diag_done(self, res):
        self.btn_diag.setEnabled(True)
        self.btn_diag.setText("Проверить")
        t = self.diag_table
        t.setRowCount(0)
        col = {"ok": "#2ecc71", "warn": "#f39c12", "err": "#e74c3c"}
        for r in res or []:
            i = t.rowCount()
            t.insertRow(i)
            a = QTableWidgetItem(r["label"])
            a.setForeground(QColor(col.get(r["level"], "#888")))
            t.setItem(i, 0, a)
            t.setItem(i, 1, QTableWidgetItem(r["detail"]))
            if r.get("fix"):
                b = QPushButton("Исправить")
                b.clicked.connect(lambda _=False, f=r["fix"]: self.apply_fix(f))
                t.setCellWidget(i, 2, b)
        t.resizeColumnToContents(0)

    def apply_fix(self, fix):
        self.log(f"исправление {fix}:\n{diag.apply_fix(fix)}")
        self.run_diag()

    def load_list(self, *_):
        p = lists.user_list_paths()[self.list_combo.currentText()]
        try:
            self.list_edit.setPlainText(p.read_text(encoding="utf-8"))
        except OSError:
            self.list_edit.setPlainText("")

    def save_list(self):
        p = lists.user_list_paths()[self.list_combo.currentText()]
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(self.list_edit.toPlainText(), encoding="utf-8")
        self.log(f"сохранён {p.name}")

    def update_ipset(self):
        self.btn_ipset.setEnabled(False)
        w = Worker(lambda log: lists.update_ipset(log))
        w.line.connect(self.bridge.log.emit)
        w.done.connect(lambda ok: self.btn_ipset.setEnabled(True))
        self._ipset_w = w
        w.start()

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
        self.tray.activated.connect(lambda r: self.show_window() if r in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick) else None)
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
        if not self._hint_shown:
            self._hint_shown = True
            self.tray.showMessage("ZapretAuto", "Программа свёрнута в трей и продолжает работать. "
                                  "Выход — правый клик по значку → «Выход».")

    def describe(self) -> str:
        c = self.settings.get("strategy")
        if not c:
            return "не выбрана"
        if c.get("picks"):
            n = len(set(c["picks"].values()) | {c["base"]})
            return f"авто-комбинация из {n} стратегий"
        return c["base"]

    def refresh(self):
        run = self.runner.running
        state = "busy" if self.busy else ("on" if run else "off")
        self.tray.setIcon(circle(COLORS[state]))
        self.setWindowIcon(circle(COLORS[state]))
        title = self.busy_text if self.busy else ("Обход включён" if run else "Обход выключен")
        self.status.setText(title)
        if self.error and not self.busy:
            self.substatus.setText(self.error)
            self.substatus.setStyleSheet("color:#f87171")
        else:
            self.substatus.setStyleSheet("")
            if not self.busy:
                self.substatus.setText(f"Стратегия: {self.describe()}" if run
                                       else "Нажмите кнопку, чтобы включить. Стратегия: " + self.describe())
        self.tray.setToolTip(f"ZapretAuto — {title}")
        self.btn_toggle.setAccessibleName(title)
        for w in (self.btn_toggle, self.combo, self.box_settings, self.btn_check_access):
            w.setEnabled(not self.busy)
        self.btn_toggle.setProperty("state", state)
        self.btn_toggle.setIcon(power_icon({"on": "#22c55e", "busy": "#f59e0b"}.get(state, "#9aa3b5")))
        self.btn_toggle.style().unpolish(self.btn_toggle)
        self.btn_toggle.style().polish(self.btn_toggle)
        self.act_toggle.setText("Выключить" if run else "Включить")
        self.btn_auto.setEnabled(not self.busy)
        c = self.settings.get("strategy")
        self.combo.setCurrentIndex(0 if not c or c.get("picks") else max(0, self.combo.findText(c["base"])))

    def save(self, **kw):
        tester.save_settings(**kw)
        self.settings = tester.load_settings()

    # ---------- runner ----------
    def start_runner(self):
        if self.busy:
            return False
        s = tester.chosen_strategy()
        if not s:
            self.error = "Стратегия не выбрана — нажмите «Подобрать лучшую стратегию»."
            self.refresh()
            return False
        try:
            self.runner.start(s, self.settings.get("game_tcp", False),
                              self.settings.get("game_udp", False) or self._game_udp_wanted())
            self.log(f"запущено: {s.name}")
            self.error = ""
            self._expect_running = True
        except Exception as e:  # noqa: BLE001
            self.log(f"ошибка запуска: {e}")
            self.error = "Не удалось запустить обход. Откройте вкладку «Диагностика» → «Проверить»."
        self.refresh()
        return self.runner.running

    def set_advanced(self, on: bool, save: bool = True):
        """Show/hide expert settings and the raw lists editor."""
        self.adv_box.setVisible(on)
        self.tabs.setTabVisible(self.tab_lists, on)
        self.btn_adv.setText("Дополнительно ▾" if on else "Дополнительно ▸")
        if save:
            self.save(show_advanced=on)

    def _health(self):
        """Background check of key services; two failed checks in a row -> re-pick automatically."""
        if self._health_running or self.busy or not self.runner.running:
            return
        self._health_running = True

        def done(res):
            self._health_running = False
            if not res or self.busy:
                return
            core = [v for k, v in res.items() if k in ("Discord", "YouTube", "Скорость YouTube")]
            if all(core):
                self._health_fails = 0
                return
            self._health_fails += 1
            bad = ", ".join(k for k, v in res.items() if not v)
            self.log(f"проверка: не работает {bad} ({self._health_fails}/2)")
            self._next_health = time.time() + 60  # re-check soon
            if self._health_fails >= 2 and self.settings.get("auto_repick", True):
                self._health_fails = 0
                self.tray.showMessage("ZapretAuto", f"Перестало работать: {bad}. Подбираю стратегию заново…")
                self.autopick()
        self._run(lambda log: tester.quick_check(), done)

    def _tick(self):
        """Detect winws dying on its own (antivirus, driver conflict)."""
        if self._expect_running and not self.busy and not self.runner.running:
            self._expect_running = False
            self.error = "Обход неожиданно остановился. Проверьте антивирус и вкладку «Диагностика»."
            self.log("winws завершился сам")
            self.tray.showMessage("ZapretAuto", self.error)
        now = time.time()
        net = tester.egress_adapter() if int(now) % 10 < 2 else self._net  # cheap: ~every 10 s
        if self._net is not None and net != self._net:
            self.log(f"сеть изменилась ({self._net} → {net}), проверю доступ")
            self._next_health = now + 20
        self._net = net
        if (not self.busy and now - self.settings.get("ipset_updated", 0) > 7 * 86400
                and now - getattr(self, "_ipset_try", 0) > 3600):
            self._ipset_try = now  # weekly; retry hourly on failure
            from app import lists
            self.log("еженедельное обновление ipset…")
            self._run(lambda log: lists.update_ipset(log),
                      lambda ok: self.start_runner() if ok and self.runner.running and not self.busy else None)
        if now >= self._next_health:
            self._next_health = now + 600
            self._health()
        self.refresh()

    def _run(self, fn, done, line=None):
        """Start a Worker and keep a reference until its thread has finished."""
        w = Worker(fn)
        w.line.connect(line or self.log)
        w.done.connect(done)
        w.finished.connect(lambda: self._workers.remove(w) if w in self._workers else None)
        self._workers.append(w)
        w.start()
        return w

    def check_access(self):
        self.substatus.setText("Проверяю доступ…")
        self.btn_check_access.setEnabled(False)

        def done(res):
            self.btn_check_access.setEnabled(True)
            if not res:
                self.substatus.setText("Не удалось проверить")
                return
            txt = "   ".join(f"{k} {'✓' if ok else '✗'}" for k, ok in res.items())
            self.log("проверка доступа: " + txt)
            self.substatus.setText(txt + ("" if all(res.values()) else
                                          "   — если что-то ✗, нажмите «Подобрать лучшую стратегию»"))
        self._run(lambda log: tester.quick_check(), done)

    def _game_udp_wanted(self) -> bool:
        return bool(self.watcher and any(g.get("ips") for g in self.watcher.games().values()))

    def toggle(self):
        if self.busy:
            return
        self.error = ""
        if self.runner.running:
            self._expect_running = False
            self.runner.stop()
            self.log("остановлено")
            self.refresh()
        elif not self.start_runner() and not self.settings.get("strategy"):
            self.ask_autopick()

    def on_combo(self, i):
        if i == 0:
            if not self.settings.get("strategy", {}).get("picks"):
                QMessageBox.information(self, "ZapretAuto", "Комбинация ещё не подобрана. "
                                        "Нажмите «Подобрать лучшую стратегию».")
                self.refresh()
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
        elif key == "telegram":
            from app import lists
            self._run(lambda log: lists.set_telegram(v, log),
                      lambda _: self.start_runner() if self.runner.running and not self.busy else None)
        elif key in ("game_tcp", "game_udp") and self.runner.running and not self.busy:
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
        self.show_window()
        if QMessageBox.question(
                self, "Первая настройка",
                "Нужно один раз подобрать стратегию под вашего провайдера.\n\n"
                "Это займёт 3–5 минут. В это время Discord и YouTube могут ненадолго "
                "пропадать — это нормально. VPN на время подбора лучше выключить.\n\n"
                "Начать?") == QMessageBox.Yes:
            self.autopick()

    def autopick(self, *_):
        if self.busy:
            return
        vpn = tester.vpn_active()
        if vpn:
            from app import vpn as vpnmod
            if not vpnmod.bind_ip():
                QMessageBox.warning(self, "VPN", f"Активен VPN ({vpn}), и проверить сеть мимо него не получается. "
                                    "Отключите VPN на время подбора.")
                return
            self.log(f"VPN {vpn} включён — подбор пойдёт мимо него")
        self._expect_running = False
        self.runner.stop()
        self.busy = True
        self.busy_text = "Подбираю стратегию…"
        self.error = ""
        self.refresh()
        self.substatus.setText("Обычно 3–5 минут. Обход на это время выключен.")
        self.tabs.setCurrentIndex(0)

        def line(t):
            self.log(t)
            m = re.match(r"\[(\d+)/(\d+)\]", t)
            if m:
                self.substatus.setText(f"Проверено стратегий: {m.group(1)} из {m.group(2)}")
            elif t.startswith("Финальная"):
                self.substatus.setText("Финальная проверка лучших вариантов…")
        self.worker = self._run(lambda log: tester.auto_pick(log), self._autopick_done, line)

    def _autopick_done(self, res):
        self.busy = False
        self.settings = tester.load_settings()
        self.log("автоподбор завершён" if res else "автоподбор не дал результата")
        if not res:
            self.error = "Подбор не удался — подробности во вкладке «Лог»."
        if res:
            self.start_runner()
            self._offer_share()
        self.refresh()

    def _offer_share(self):
        lp = self.settings.get("last_pick", {})
        if lp.get("shared") or self.settings.get("share_never") or not lp.get("total"):
            return
        box = QMessageBox(self)
        box.setWindowTitle("Поделиться результатом")
        box.setText("Подбор завершён. Отправить результат в общую базу?\n\n"
                    "Другие пользователи вашего провайдера смогут настроиться за секунды. "
                    "Отправляется только номер сети провайдера (AS) и найденная комбинация — "
                    "без вашего IP. Откроется страница GitHub (нужен аккаунт).")
        yes = box.addButton("Поделиться", QMessageBox.AcceptRole)
        box.addButton("Не сейчас", QMessageBox.RejectRole)
        never = box.addButton("Больше не спрашивать", QMessageBox.DestructiveRole)
        box.exec()
        if box.clickedButton() is never:
            self.save(share_never=True)
        elif box.clickedButton() is yes:
            def done(url):
                if url:
                    import webbrowser
                    webbrowser.open(url)
                else:
                    self.log("не удалось определить провайдера")

            def make(log):
                from app import __version__, sharedb
                who = sharedb.isp()
                if not who:
                    return None
                return sharedb.report_url(who[0], who[1], updater.current_version(), __version__,
                                          self.settings.get("strategy", {}), lp["score"], lp["total"])
            self._run(make, done)

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
        if self.runner.running and not self.busy:
            self.start_runner()  # restart with game filters
            msg = "Добавлены IP игры. Если игра не подключается — перезапустите её."
            self.log(msg)
            self.tray.showMessage("ZapretAuto", msg)

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

    def reset_game(self):
        r = self.table.currentRow()
        if r < 0 or not self.watcher:
            return
        exe = self.table.item(r, 0).text()
        self.watcher.reset_game(exe)
        self.log(f"IP игры сброшены: {exe}")
        self.fill_games()

    def add_game(self):
        path, _ = QFileDialog.getOpenFileName(self, "Выберите exe игры", "", "Программы (*.exe)")
        if path:
            exe = path.replace("/", "\\")
            self.save(games_force=sorted(set(self.settings.get("games_force", [])) | {exe}))
            if self.watcher and hasattr(self.watcher, "add_manual"):
                self.watcher.add_manual(exe)
            self.log(f"игра добавлена: {exe}")

    # ---------- updates ----------
    def check_update(self):
        if updater:
            self.log("проверка обновлений…")
            updater.check_async(lambda tag, newer: self.bridge.update_checked.emit(tag, newer))
            if hasattr(updater, "app_check_async"):
                updater.app_check_async(lambda tag, newer: self.bridge.app_checked.emit(tag, newer))

    def on_app_checked(self, tag, newer):
        if not newer:
            return
        self.log(f"доступна новая версия ZapretAuto: {tag}")
        if self.settings.get("auto_update", True) or QMessageBox.question(
                self, "ZapretAuto", f"Установить ZapretAuto {tag}?") == QMessageBox.Yes:
            def done(ok):
                if ok is True:  # installer replaces files and restarts the app
                    self.tray.showMessage("ZapretAuto", f"Устанавливаю версию {tag}, программа перезапустится…")
                    QTimer.singleShot(1500, self.quit)
                else:
                    self.log("самообновление не выполнено")
            self._run(lambda log: updater.app_update(log), done)

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
        self.busy_text = "Обновляю zapret…"
        self._expect_running = False
        self.refresh()
        self.worker = self._run(lambda log: updater.update(log, self.runner.stop),
                                lambda ok: self._update_done(ok, was, auto))

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
