"""Tray icon + main window."""
import re
import subprocess
import sys
import time
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QAction, QColor
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QFileDialog, QFrame, QGridLayout, QGroupBox, QHeaderView, QHBoxLayout, QInputDialog, QLabel,
                               QMenu, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QSystemTrayIcon,
                               QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget)

from app import diag, lists, logs, stats, strategies, tester
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
from app.gui.style import COLORS, STYLE, app_icon, power_icon  # noqa: E402
from app.gui.worker import Bridge, Worker  # noqa: E402


class MainWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("ZapretAuto")
        self.resize(700, 680)
        self.runner = Runner()
        self.watcher = None
        self.worker: Worker | None = None
        self.busy = False
        self._cancel = False  # set by «Отменить» during a pick
        self._cancellable = False
        self._profile_exe: str | None = None  # game whose own strategy is active
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
        logs._on_crash = lambda msg: self.bridge.log.emit(f"внутренняя ошибка: {msg} (подробности в журнале)")
        self._health_fails = 0
        self._health_running = False
        self._last_tick = time.time()
        self._crashes: list[float] = []
        self._repick_pending = False
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
        self.chips = QHBoxLayout()
        self.chips.setSpacing(6)
        chips_w = QWidget()
        chips_w.setLayout(self.chips)
        chips_w.setObjectName("chipsrow")
        self.chips.setContentsMargins(0, 2, 0, 2)
        col.addWidget(chips_w)
        self.btn_check_access = QPushButton("Проверить доступ")
        self.btn_check_access.setToolTip("Проверить, открываются ли Discord, YouTube и голосовой чат прямо сейчас")
        self.btn_check_access.setCursor(Qt.PointingHandCursor)
        self.btn_check_access.clicked.connect(lambda: self.check_access())
        brow = QHBoxLayout()
        brow.addWidget(self.btn_check_access)
        self.btn_site = QPushButton("Сайт не открывается…")
        self.btn_site.setToolTip("Проверить конкретный сайт и, если поможет, добавить его в обход")
        self.btn_site.setCursor(Qt.PointingHandCursor)
        self.btn_site.clicked.connect(self.check_site)
        brow.addWidget(self.btn_site)
        self.btn_cancel = QPushButton("Отменить")
        self.btn_cancel.setToolTip("Остановить подбор; обход вернётся к прежней стратегии")
        self.btn_cancel.clicked.connect(self.cancel_busy)
        self.btn_cancel.hide()
        brow.addWidget(self.btn_cancel)
        brow.addStretch()
        col.addLayout(brow)
        col.addStretch()
        hl.addLayout(col, 1)
        lay.addWidget(head)
        self.progress = QProgressBar(objectName="thin")
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(4)
        self.progress.setRange(0, 0)
        self.progress.hide()
        lay.addWidget(self.progress)

        # strategy row
        row = QHBoxLayout()
        self.btn_auto = QPushButton("Подобрать лучшую стратегию", objectName="primary")
        self.btn_auto.setCursor(Qt.PointingHandCursor)
        self.btn_auto.setToolTip("Проверить все стратегии на Discord, YouTube и голосовом трафике "
                                 "и собрать лучшую комбинацию. Занимает 2–4 минуты.")
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
            ("auto_hostlist", "Сам находить заблокированные сайты", True,
             "winws сам замечает сайты, которые не открываются из-за DPI, и включает для них обход. "
             "Найденное видно во вкладке «Списки» → list-auto.txt (можно удалить лишнее).", True),
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
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setStretchLastSection(True)
        self.games_empty = QLabel(objectName="empty")
        self.games_empty.setAlignment(Qt.AlignCenter)
        self.games_empty.setWordWrap(True)
        gl.addWidget(self.games_empty, 1)
        gl.addWidget(self.table)
        r = QHBoxLayout()
        b1 = QPushButton("Игнорировать")
        b1.setToolTip("Больше не считать выбранную программу игрой")
        b1.clicked.connect(self.ignore_game)
        b2 = QPushButton("Добавить игру (exe)…")
        b2.setToolTip("Выбрать exe игры вручную, если она не нашлась сама")
        b2.clicked.connect(self.add_game)
        b3 = QPushButton("Сбросить игру")
        b3.setToolTip("Убрать собранные адреса выбранной игры и её собственный профиль стратегии")
        b3.clicked.connect(self.reset_game)
        r.addWidget(b1)
        r.addWidget(b2)
        r.addWidget(b3)
        gl.addLayout(r)
        self.btn_gamepick = QPushButton("Подобрать стратегию для этой игры", objectName="primary")
        self.btn_gamepick.setToolTip("Если игра не входит в онлайн («Вход в систему…»): программа сама запускает игру "
                                     "с разными стратегиями и оставляет ту, с которой вход проходит, а Discord и "
                                     "YouTube работают. Занимает до 15 минут, игру в это время не трогайте.")
        self.btn_gamepick.clicked.connect(self.pick_for_game)
        gl.addWidget(self.btn_gamepick)
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
        drow = QHBoxLayout()
        drow.addWidget(self.btn_diag)
        self.btn_report = QPushButton("Сохранить отчёт об ошибке…")
        self.btn_report.setToolTip("Собрать журнал, диагностику и настройки в zip, чтобы отправить разработчику. "
                                   "Имя пользователя Windows в отчёте скрыто.")
        self.btn_report.clicked.connect(self.save_report)
        drow.addWidget(self.btn_report)
        drow.addStretch()
        dl.addLayout(drow)
        self.diag_table = QTableWidget(0, 3)
        self.diag_table.setHorizontalHeaderLabels(["Проверка", "Результат", ""])
        hh = self.diag_table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.Stretch)
        hh.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.diag_table.verticalHeader().hide()
        self.diag_table.setWordWrap(True)
        self.diag_table.setEditTriggers(QTableWidget.NoEditTriggers)
        dl.addWidget(self.diag_table)
        self.tabs.addTab(d, "Диагностика")
        st = QWidget()
        stl = QVBoxLayout(st)
        self.lbl_stats = QLabel()
        self.lbl_stats.setWordWrap(True)
        self.lbl_stats.setTextFormat(Qt.RichText)
        stl.addWidget(self.lbl_stats)
        stl.addStretch()
        self.tabs.addTab(st, "Статистика")
        self.tabs.currentChanged.connect(lambda i: self.fill_stats() if self.tabs.widget(i) is st else None)
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
        self.btn_diag.setText("Найти проблемы")
        self._last_diag = res
        t = self.diag_table
        t.setRowCount(0)
        col = {"ok": "#4ade80", "warn": "#fbbf24", "err": "#f87171"}
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
        t.resizeRowsToContents()

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
        text = self.list_edit.toPlainText()
        if not any(l.strip() and not l.strip().startswith("#") for l in text.splitlines()):
            # winws refuses empty lists (and re-reads them live): keep a harmless placeholder entry
            text = text.rstrip() + ("\n" if text.strip() else "") + (
                "203.0.113.113/32" if p.name.startswith("ipset") else "domain.example.abc") + "\n"
            self.list_edit.setPlainText(text)
        p.write_text(text, encoding="utf-8")
        self.log(f"сохранён {p.name}")

    def update_ipset(self):
        self.btn_ipset.setEnabled(False)
        self._run(lambda log: lists.update_ipset(log), lambda ok: self.btn_ipset.setEnabled(True))

    def _tray(self):
        self.tray = QSystemTrayIcon(app_icon(COLORS["off"]), self)
        m = QMenu()
        self.act_toggle = QAction("Включить", m)
        self.act_toggle.triggered.connect(self.toggle)
        a2 = QAction("Подобрать стратегию", m)
        a2.triggered.connect(self.autopick)
        a3 = QAction("Открыть", m)
        a3.triggered.connect(self.show_window)
        a5 = QAction("Проверить доступ", m)
        a5.triggered.connect(lambda: self.check_access(notify=True))
        a6 = QAction("Выключить на 15 минут", m)
        a6.triggered.connect(lambda: self.pause(15))
        a4 = QAction("Выход", m)
        a4.triggered.connect(self.quit)
        for a in (self.act_toggle, a6, a5, a2, a3, a4):
            m.addAction(a)
        self._menu = m
        self.tray.setContextMenu(m)
        self.tray.activated.connect(lambda r: self.show_window() if r in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick) else None)
        self.tray.show()

    # ---------- helpers ----------
    def log(self, text: str):
        self.logbox.appendPlainText(time.strftime("%H:%M:%S ") + text)
        logs.log.info(text)

    def showEvent(self, e):
        super().showEvent(e)
        if not getattr(self, "_dark_title", False):
            self._dark_title = True
            try:
                import ctypes
                hwnd, on = int(self.winId()), ctypes.c_int(1)
                for attr in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE (new / pre-20H1 builds)
                    if ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(on), 4) == 0:
                        break
            except Exception:  # noqa: BLE001
                pass

    def set_chips(self, res: dict):
        """Colored pills: service ✓/✗ after an access check."""
        while self.chips.count():
            it = self.chips.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        for name, ok in res.items():
            c = QLabel(f"{'✓' if ok else '✗'} {name}", objectName="chip")
            c.setProperty("ok", "yes" if ok else "no")
            self.chips.addWidget(c)
        self.chips.addStretch()

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
        if self._profile_exe:
            return f"профиль игры {self._profile_exe.rsplit('.', 1)[0]}"
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
        if getattr(self, "_icon_state", None) != state:  # redraw only on change
            self._icon_state = state
            self.tray.setIcon(app_icon(COLORS[state]))
            self.setWindowIcon(app_icon(COLORS[state]))
        self.progress.setVisible(self.busy)
        self.btn_cancel.setVisible(self.busy and self._cancellable)
        if not self.busy:
            self.progress.setRange(0, 0)
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
        last = getattr(self, "_last_check", None)
        tip = f"ZapretAuto — {title}\n{self.describe()}"
        if last:
            ok = "всё работает" if last[1] else "есть сбои"
            tip += f"\nпроверка {time.strftime('%H:%M', time.localtime(last[0]))}: {ok}"
        self.tray.setToolTip(tip[:127])  # Windows truncates tray tooltips at 127 chars
        self.btn_toggle.setAccessibleName(title)
        for w in (self.btn_toggle, self.combo, self.box_settings, self.btn_check_access, self.btn_site,
                  self.btn_gamepick):
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
        prof = self.settings.get("game_profiles", {}).get(self._profile_exe or "")
        if prof:
            from app import gamepick
            try:
                s = gamepick.build(prof)
            except KeyError:  # strategy vanished after a zapret update
                pass
        if not s:
            self.error = "Стратегия не выбрана — нажмите «Подобрать лучшую стратегию»."
            self.refresh()
            return False
        try:
            flags = self._game_flags()
            self.runner.start(s, *flags, self.settings.get("auto_hostlist", True))
            self._run_flags = flags
            self.log(f"запущено: {s.name}")
            stats.record("start")
            self.error = ""
            self._expect_running = True
        except Exception as e:  # noqa: BLE001
            self.log(f"ошибка запуска: {e}")
            self.error = "Не удалось запустить обход. Откройте вкладку «Диагностика» → «Найти проблемы»."
            self._explain_failure()
        self.refresh()
        return self.runner.running

    def save_report(self):
        default = str(Path.home() / "Desktop" / f"ZapretAuto-report-{time.strftime('%Y%m%d-%H%M')}.zip")
        path, _ = QFileDialog.getSaveFileName(self, "Сохранить отчёт", default, "Zip (*.zip)")
        if not path:
            return
        diag_rows = getattr(self, "_last_diag", None)

        def done(p):
            if p:
                self.log(f"отчёт сохранён: {p}")
                QMessageBox.information(self, "ZapretAuto", f"Отчёт сохранён:\n{p}\n\nОтправьте этот файл разработчику.")
        self._run(lambda log: logs.make_report(Path(path), diag_rows), done)

    def fill_stats(self):
        s = stats.summary(7)
        last = time.strftime("%d.%m %H:%M", time.localtime(s["last_repick"])) if s["last_repick"] else "не было"
        pct = f"{s['ok_pct']}%" if s["ok_pct"] is not None else "ещё нет данных"
        self.lbl_stats.setText(
            f"<h3>За последние {s['days']} дней</h3>"
            f"<p>Обход работал: <b>{s['uptime_h']} ч</b></p>"
            f"<p>Фоновых проверок: <b>{s['checks']}</b>, успешных: <b>{pct}</b></p>"
            f"<p>Программа сама чинила обход: <b>{s['repicks']}</b> раз (последний: {last})</p>"
            f"<p>Неожиданных остановок winws: <b>{s['crashes']}</b></p>"
            f"<p>Обновлений списка ipset: <b>{s['ipset']}</b></p>"
            + ("<p>Смены стратегии: " + ", ".join(f"{time.strftime('%d.%m %H:%M', time.localtime(t))} → {n}"
                                                for t, n in s["switches"][-5:]) + "</p>" if s["switches"] else ""))

    def set_advanced(self, on: bool, save: bool = True):
        """Show/hide expert settings and the raw lists editor."""
        self.adv_box.setVisible(on)
        self.tabs.setTabVisible(self.tab_lists, on)
        self.btn_adv.setText("Дополнительно ▾" if on else "Дополнительно ▸")
        if save:
            self.save(show_advanced=on)

    def _explain_failure(self):
        """Run diagnostics in the background and put the concrete cause into the status line."""
        def done(cause):
            if cause:
                self.error = "Обход не работает. " + cause
                self.log("причина: " + cause)
                self.tray.showMessage("ZapretAuto", self.error)
                self.refresh()
        self._run(lambda log: diag.first_problem(), done)

    def pause(self, minutes: int):
        """Turn the bypass off for a while (e.g. a site that dislikes it) and back on automatically."""
        if self.busy or not self.runner.running:
            return
        self._expect_running = False
        self.runner.stop()
        stats.record("stop")
        self._resume_at = time.time() + minutes * 60
        self.log(f"обход выключен на {minutes} мин")
        self.tray.showMessage("ZapretAuto", f"Обход выключен на {minutes} минут, потом включится сам.")
        self.refresh()

    def cancel_busy(self):
        self._cancel = True
        self.btn_cancel.setEnabled(False)
        self.substatus.setText("Отменяю… (дождусь окончания текущей проверки)")

    def _game_running(self) -> bool:
        return bool(self.watcher and any(g.get("active") for g in self.watcher.games().values()))

    def check_site(self):
        """'Site does not open': test it, add to the bypass list, re-test, explain the outcome."""
        text, ok = QInputDialog.getText(self, "Сайт не открывается", "Адрес сайта (например example.com):")
        dom = re.sub(r"^(https?://)?([^/:?#]+).*$", r"\2", (text or "").strip()).lower()
        if not ok or not re.fullmatch(r"[a-z0-9.-]+\.[a-z]{2,}", dom):
            return

        def work(log):
            from app import lists, vpn
            tester.BIND = vpn.bind_ip() if tester.vpn_active() else None  # test the direct path
            t = tester.Target("site", f"https://{dom}")
            if not self.runner.running:
                return "Сначала включите обход (большая кнопка), потом проверьте сайт."
            before = tester.probe(t) is not None
            if before:
                return f"{dom} уже открывается — обход для него не нужен."
            added = lists.add_user_domain(dom)
            time.sleep(1.0)  # winws re-reads the changed list by itself (on the next packet): no restart
            if any(tester.probe(t) is not None for _ in range(3)):  # first tries through DPI can be reset
                return f"Готово: {dom} добавлен в обход и открывается."
            ip_ok = tester.probe(tester.Target("ip", f"tcp://{dom}:443")) is not None
            if added:
                lists.remove_user_domain(dom)
            if not ip_ok:
                return f"{dom} заблокирован по IP-адресу — zapret тут не поможет, нужен VPN. [ip]"
            return f"Не помогло для {dom}. Попробуйте «Подобрать лучшую стратегию»."

        def done(msg):
            if msg:
                if msg.endswith(" [ip]"):  # IP-blocked: hand the domain over for the VPN's site list
                    msg = msg[:-5]
                    QApplication.clipboard().setText(dom)
                    msg += (f" Адрес «{dom}» скопирован — вставьте его в список сайтов раздельного "
                            "туннелирования вашего VPN.")
                self.log(msg)
                QMessageBox.information(self, "Проверка сайта", msg)
        self.log(f"проверяю {dom}…")
        self._run(work, done)

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
            stats.record("check", ok=all(core))
            self._last_check = (time.time(), all(core))
            self.set_chips(res)
            if all(core):
                self._health_fails = 0
                return
            self._health_fails += 1
            bad = ", ".join(k for k, v in res.items() if not v)
            self.log(f"проверка: не работает {bad} ({self._health_fails}/2)")
            self._next_health = time.time() + 60  # re-check soon
            if self._health_fails >= 2 and self.settings.get("auto_repick", True):
                self._health_fails = 0
                if self._game_running():
                    if not self._repick_pending:
                        self._repick_pending = True
                        self.tray.showMessage("ZapretAuto", f"Перестало работать: {bad}. "
                                              "Починю после игры, чтобы не прерывать матч.")
                    return
                if time.time() - self.settings.get("last_auto_repick", 0) < 6 * 3600:
                    self.log("автоподбор уже был недавно — жду, чтобы не дёргать обход")
                    return
                self.save(last_auto_repick=time.time())
                stats.record("repick", reason=bad)
                self.tray.showMessage("ZapretAuto", f"Перестало работать: {bad}. Подбираю стратегию заново…")
                self._repair = True
                self.autopick()
        self._run(lambda log: tester.quick_check(light=True), done)  # no 1 MB speed test every 10 min

    def _check_profiles(self):
        """Switch to a game's own strategy while it runs (set by «Подобрать стратегию для этой игры»)."""
        profiles = self.settings.get("game_profiles", {})
        if not profiles or self.busy:
            return
        import psutil
        try:
            running = {(p.info["name"] or "").lower() for p in psutil.process_iter(["name"])}
        except psutil.Error:
            return
        exe = next((e for e in profiles if e in running), None)
        if exe != self._profile_exe:
            self._profile_exe = exe
            if self.runner.running:
                self.start_runner()
            msg = (f"Запущена {exe.rsplit('.', 1)[0]} — включён её профиль обхода" if exe
                   else "Игра закрыта — возвращена общая стратегия")
            self.log(msg)
            if exe:
                self.tray.showMessage("ZapretAuto", msg)

    def _tick(self):
        """Detect winws dying on its own (antivirus, driver conflict)."""
        now = time.time()
        self._ticks = getattr(self, "_ticks", 0) + 1
        resume = getattr(self, "_resume_at", 0)
        if resume and time.time() >= resume:
            self._resume_at = 0
            if not self.runner.running and not self.busy:
                self.start_runner()
                self.tray.showMessage("ZapretAuto", "Обход снова включён.")
        if self._ticks % 5 == 0:  # ~10 s
            self._check_profiles()
        if self._ticks % 30 == 0 and self.runner.running and not self.busy:  # ~1 min: VPN may have moved
            from app import vpn
            self._run(lambda log: vpn.write_exclude(), lambda _: None)  # winws reloads the file itself
        if now - self._last_tick > 60:  # timer is 2 s: a long gap means sleep/hibernate
            self.log("пробуждение после сна — перезапускаю обход")
            if self._expect_running and not self.busy:
                self.start_runner()
            self._next_health = now + 30
        self._last_tick = now
        if self._expect_running and not self.busy and not self.runner.running:
            stats.record("crash")
            self._crashes = [t for t in self._crashes if now - t < 600] + [now]
            if len(self._crashes) <= 3:
                self.log(f"winws завершился сам — перезапуск ({len(self._crashes)}/3)")
                self.start_runner()
            else:
                self._expect_running = False
                self.error = "Обход постоянно падает. Проверьте антивирус и вкладку «Диагностика»."
                self.log("winws падает слишком часто — автоперезапуск остановлен")
                self.tray.showMessage("ZapretAuto", self.error)
                self._explain_failure()
        if self._repick_pending and not self.busy and not self._game_running():
            self._repick_pending = False
            self.log("игра закрыта — чиню обход")
            self.autopick()
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
            def ipset_done(ok):
                if ok:
                    stats.record("ipset")  # winws re-reads ipset-all.txt by itself
            self._run(lambda log: lists.update_ipset(log), ipset_done)
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

    def check_access(self, notify: bool = False):
        self.substatus.setText("Проверяю доступ…")
        self.btn_check_access.setEnabled(False)

        def done(res):
            self.btn_check_access.setEnabled(True)
            if not res:
                self.substatus.setText("Не удалось проверить")
                return
            txt = "   ".join(f"{k} {'✓' if ok else '✗'}" for k, ok in res.items())
            self.set_chips(res)
            self.log("проверка доступа: " + txt)
            self.substatus.setText("Всё работает" if all(res.values()) else
                                   "Если что-то ✗ — нажмите «Подобрать лучшую стратегию»")
            if notify:  # asked from the tray menu: answer there too
                self.tray.showMessage("ZapretAuto", txt)
        self._run(lambda log: tester.quick_check(), done)

    def _game_udp_wanted(self) -> bool:
        """A game was found: enable the game filter for TCP and UDP (some games log in over TCP high ports)."""
        return bool(self.watcher and any(g.get("ips") for g in self.watcher.games().values()))

    def toggle(self):
        if self.busy:
            return
        self.error = ""
        self._resume_at = 0  # a manual on/off cancels a timed pause
        if self.runner.running:
            self._expect_running = False
            self.runner.stop()
            stats.record("stop")
            self.set_chips({})
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
            self._run(lambda log: lists.set_telegram(v, log), lambda _: None)  # winws reloads the file itself
        elif key in ("game_tcp", "game_udp", "auto_hostlist") and self.runner.running and not self.busy:
            self.start_runner()

    def set_autostart(self, on: bool):
        if on:
            tr = (f'"{sys.executable}" --tray' if getattr(sys, "frozen", False)
                  else f'"{sys.executable}" "{ROOT / "main.py"}" --tray')
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
                "Это займёт 2–4 минуты. В это время Discord и YouTube могут ненадолго "
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
        self.substatus.setText("Обычно 2–4 минуты. Обход на это время выключен.")
        self.tabs.setCurrentIndex(0)

        t_start = time.time()

        def line(t):
            self.log(t)
            m = re.match(r"\[(\d+)/(\d+)\]", t)
            if m:
                done, total = int(m.group(1)), int(m.group(2))
                # remaining strategies + ~8 final re-tests, at the pace measured so far
                left = (time.time() - t_start) / done * (total - done + 8)
                eta = f", осталось около {max(1, round(left / 60))} мин" if done >= 2 else ""
                self.substatus.setText(f"Проверено стратегий: {done} из {total}{eta}")
                self.progress.setRange(0, int(m.group(2)) + 4)
                self.progress.setValue(int(m.group(1)))
            elif t.startswith("Финальная"):
                self.substatus.setText("Финальная проверка лучших вариантов…")
                self.progress.setValue(self.progress.maximum() - 2)
        self._cancel, self._cancellable = False, True
        self.worker = self._run(lambda log: tester.auto_pick(log, stop=lambda: self._cancel), self._autopick_done, line)

    def _autopick_done(self, res):
        self.busy = False
        before = (self.settings.get("strategy") or {})
        self.settings = tester.load_settings()
        after = self.settings.get("strategy") or {}
        if res and after != before:
            stats.record("switch", to=after.get("base", "?") + (" + микс" if after.get("picks") else ""))
        cancelled = self._cancel
        self._cancel = self._cancellable = False
        self.btn_cancel.setEnabled(True)
        self.log("автоподбор завершён" if res else ("подбор отменён" if cancelled else "автоподбор не дал результата"))
        if not res and not cancelled:
            self.error = "Подбор не удался — подробности во вкладке «Лог»."
        if res or self.settings.get("strategy"):
            self.start_runner()  # the pick stopped the bypass: always bring it back (old strategy if pick failed)
        if getattr(self, "_repair", False):  # this pick was an automatic repair: tell how it ended
            self._repair = False
            self.tray.showMessage("ZapretAuto", f"Обход починен: {self.describe()}" if res else
                                  "Починить автоматически не удалось — откройте окно программы")
        if res:
            self._offer_autostart()
            self._offer_share()
        self.refresh()

    def _offer_autostart(self):
        """After the first successful setup: most people want the bypass to come up with Windows."""
        if self.settings.get("autostart") or self.settings.get("autostart_asked"):
            return
        self.save(autostart_asked=True)
        if QMessageBox.question(self, "ZapretAuto", "Готово! Запускать ZapretAuto вместе с Windows?\n\n"
                                "Программа будет тихо стартовать в трее, и Discord/YouTube заработают сразу "
                                "после включения компьютера.") == QMessageBox.Yes:
            self.cb["autostart"].setChecked(True)  # saves the setting and creates the task

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

    def _game_flags(self) -> tuple[bool, bool]:
        wanted = self._game_udp_wanted()
        return (self.settings.get("game_tcp", False) or wanted, self.settings.get("game_udp", False) or wanted)

    def _apply_game_flags(self):
        """Restart winws to switch the game filter on, but not while a game is logging in."""
        if not self.runner.running or self.busy or self._game_flags() == getattr(self, "_run_flags", None):
            return
        from app.gamewatch import LOGIN_GRACE
        games = self.watcher.games() if self.watcher else {}
        wait = max([LOGIN_GRACE - (time.time() - g.get("started", 0)) for g in games.values() if g.get("active")]
                   or [0])
        if wait > 0:
            QTimer.singleShot(int(wait * 1000) + 500, self._apply_game_flags)
            return
        self.start_runner()

    def on_games_changed(self):
        self.fill_games()
        if self.runner.running and not self.busy:
            # winws re-reads the changed ipset-games.txt by itself; restart only when the game filter ports
            # have to be switched on (first game) — restarts would drop live match connections
            if self._game_flags() != getattr(self, "_run_flags", None):
                self._apply_game_flags()
            games = self.watcher.games() if self.watcher else {}
            known = set(self.settings.get("games_notified", []))
            new = [exe for exe, g in games.items() if g.get("ips") and exe not in known]
            if new:  # tell once per game; later additions are routine and silent
                self.save(games_notified=sorted(known | set(new)))
                names = ", ".join(n.rsplit(".", 1)[0] for n in new)
                msg = f"Найдена игра: {names}. Обход для её серверов включён."
                self.tray.showMessage("ZapretAuto", msg)
            else:
                msg = "обновлены адреса игровых серверов"
            self.log(msg)

    def fill_games(self):
        games = self.watcher.games() if self.watcher else {}
        ign = set(self.settings.get("games_ignore", []))
        rows = [(k, v) for k, v in games.items() if k not in ign]
        self.games_empty.setVisible(not rows)
        self.games_empty.setText(
            "Пока игр не найдено. Запустите игру — она появится здесь сама." if self.settings.get("game_watch") else
            "Включите «Следить за играми» в настройках — запущенные игры появятся здесь сами.")
        self.table.setVisible(bool(rows))
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

    def pick_for_game(self):
        r = self.table.currentRow()
        games = self.watcher.games() if self.watcher else {}
        exe = self.table.item(r, 0).text() if r >= 0 else None
        path = (games.get(exe) or {}).get("path") or (self.settings.get("games", {}).get(exe) or {}).get("path")
        if not path:
            path, _ = QFileDialog.getOpenFileName(self, "Выберите exe игры", "", "Программы (*.exe)")
            if not path:
                return
        if QMessageBox.question(self, "Подбор для игры",
                                f"{Path(path).name}\n\nПрограмма будет сама запускать и закрывать игру с разными "
                                "стратегиями (до 15 минут). Не трогайте игру, пока идёт подбор.\n\n"
                                "Начать?") != QMessageBox.Yes:
            return
        from app import gamepick
        self._expect_running = False
        self.busy, self.busy_text, self.error = True, "Подбираю стратегию для игры…", ""
        self.refresh()
        self.tabs.setCurrentIndex(0)

        def done(res):
            self.busy = False
            cancelled = self._cancel
            self._cancel = self._cancellable = False
            self.btn_cancel.setEnabled(True)
            self.settings = tester.load_settings()
            self.start_runner()  # the pick stopped the bypass: bring it back in any case
            if res:
                self.tray.showMessage("ZapretAuto", "Стратегия для игры подобрана — можно играть.")
            elif cancelled:
                self.log("подбор для игры отменён")
            else:
                self.error = "Для этой игры подходящей стратегии не нашлось — попробуйте VPN."
            self.refresh()
        def line(t):
            self.log(t)
            self.substatus.setText(t if len(t) < 120 else t[:117] + "…")  # current step in the header
        self._cancel, self._cancellable = False, True
        self.worker = self._run(lambda log: gamepick.pick_for_game(path, self.runner, log, stop=lambda: self._cancel),
                                done, line)

    def reset_game(self):
        r = self.table.currentRow()
        if r < 0 or not self.watcher:
            return
        exe = self.table.item(r, 0).text()
        self.watcher.reset_game(exe)
        profiles = dict(self.settings.get("game_profiles", {}))
        if profiles.pop(exe.lower(), None) is not None:
            self.save(game_profiles=profiles)
            self.log(f"профиль стратегии игры удалён: {exe}")
            if self._profile_exe == exe.lower():
                self._profile_exe = None
                if self.runner.running:
                    self.start_runner()
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
        if self.settings.get("autostart"):
            self.set_autostart(True)  # refresh the task (adds --tray for older installs)
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
        self._cancel = True  # let a running pick stop at its next step
        self.runner.stop()
        self.stop_watcher()
        self.tray.hide()
        for w in list(self._workers):  # a QThread destroyed while running aborts the process on exit
            w.wait(3000)
        QApplication.quit()
