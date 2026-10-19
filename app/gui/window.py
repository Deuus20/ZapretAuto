"""Tray icon + main window."""
import re
import subprocess
import sys
import time
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QColor
from PySide6.QtWidgets import (QApplication, QComboBox, QFileDialog, QFrame, QHeaderView, QHBoxLayout, QInputDialog,
                               QLabel, QLineEdit, QMenu, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton,
                               QScrollArea, QSystemTrayIcon, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

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
AUTO = "Авто (результат подбора)"
UPDATE_EVERY = 6 * 3600  # s between background update checks
from app.gui import style  # noqa: E402
from app.gui.style import COLORS, app_icon, init_fonts  # noqa: E402
from app.gui.widgets import InputRow, PowerButton, Pages, Rail, SettingRow, StatusChip, Switch  # noqa: E402
from app.gui.worker import Bridge, Worker  # noqa: E402


class MainWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("ZapretAuto")
        self.resize(800, 560)
        self.setMinimumSize(680, 480)
        self.runner = Runner()
        self.watcher = None
        self.busy = False
        self._cancel = False  # set by «Отменить» during a pick
        self._cancellable = False
        self.busy_text = ""
        self.error = ""
        self._workers: list = []  # keep workers referenced until finished
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
        self._next_health = time.time() + 30  # status chips soon after start

    # ---------- UI ----------
    SETTINGS = [  # key, title, supporting text, default, section
        ("game_watch", "Следить за играми",
         "Находить запущенные игры и включать обход для их серверов. Если с обходом стало хуже — адрес убирается.",
         False, "main"),
        ("auto_repick", "Чинить обход самостоятельно",
         "Каждые 10 минут и при смене сети проверять Discord и YouTube. Если не открываются дважды подряд — "
         "подобрать стратегию заново (не посреди игры).", True, "main"),
        ("auto_update", "Обновляться автоматически",
         "Ставить новые версии zapret и ZapretAuto при запуске и раз в 6 часов, но не во время игры.", True, "main"),
        ("autostart", "Запускать вместе с Windows",
         "ZapretAuto тихо стартует в трее при входе в Windows.", False, "main"),
        ("auto_hostlist", "Находить заблокированные сайты",
         "Обход сам замечает сайты, которые не открываются из-за DPI, и включает для них обход. "
         "Найденное — в разделе «Списки» → list-auto.txt.", True, "adv"),
        ("game_udp", "Обход UDP для игр",
         "Обход UDP-трафика игр на портах 1024–65535. Включается сам, когда найдена игра.", False, "adv"),
        ("game_tcp", "Обход TCP для игр",
         "То же для TCP: некоторые игры входят в онлайн по TCP. Включается сам, когда найдена игра. "
         "Порт 1935 (трансляции OBS) не трогает.", False, "adv"),
        ("telegram", "Telegram (эксперимент)",
         "Добавить подсети Telegram в обход. Помогает не всем провайдерам: при блокировке по IP нужен прокси или VPN.",
         False, "adv"),
        ("zapret2", "zapret2 (эксперимент)",
         "Новый движок: если сайт начинает блокироваться, обход сам переключает для него другую стратегию — "
         "без перезапуска и подбора. При первом включении скачивается ~4 МБ.", False, "adv"),
    ]

    def _build(self):
        QApplication.instance().setStyle("Fusion")
        init_fonts()
        self._light = None
        self.apply_theme(force=True)
        self.setObjectName("root")
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        rail = Rail()
        outer.addWidget(rail)
        self.tabs = Pages(rail)
        outer.addWidget(self.tabs, 1)

        self.tabs.addTab(self._page_home(), "Главная", "home")
        self.page_games = self._page_games()
        self.tabs.addTab(self.page_games, "Игры", "games")
        self.tabs.addTab(self._page_settings(), "Настройки", "settings")
        self.tabs.addTab(self._page_diag(), "Диагностика", "diag")
        log = self._page_log()
        self.tabs.addTab(log, "Журнал", "log")
        self.tabs.currentChanged.connect(lambda i: self.fill_stats() if self.tabs.widget(i) is log else None)
        self.tabs.currentChanged.connect(lambda i: self.fill_games() if self.tabs.widget(i) is self.page_games
                                         else None)
        lists_page = self._page_lists()
        self.tabs.addTab(lists_page, "Списки", "lists")
        from PySide6.QtGui import QKeySequence, QShortcut
        for i in range(self.tabs.count()):  # keyboard navigation for the rail
            QShortcut(QKeySequence(f"Ctrl+{i + 1}"), self, activated=lambda i=i: self.tabs.setCurrentIndex(i)
                      if not self.tabs.rail.buttons[i].isHidden() else None)  # Lists: only with «Дополнительно»
        self.tab_lists = self.tabs.indexOf(lists_page)
        self.load_list()
        self.set_advanced(bool(self.settings.get("show_advanced")), save=False)
        ver = "недоступно"
        if updater:
            try:
                ver = updater.current_version()
            except Exception:  # noqa: BLE001
                pass
        from app import __version__
        self.lbl_ver.setText(f"ZapretAuto {__version__} · zapret {ver}")
        if not updater:
            self.btn_check.setEnabled(False)
            self.btn_update.setEnabled(False)
        if not gamewatch:
            self.cb["game_watch"].setEnabled(False)
        self.fill_games()  # home games card text, also when watching is off

    @staticmethod
    def _page(margins=(20, 16, 20, 16)) -> tuple[QWidget, QVBoxLayout]:
        w = QWidget(objectName="page")
        lay = QVBoxLayout(w)
        lay.setContentsMargins(*margins)
        lay.setSpacing(10)
        return w, lay

    @staticmethod
    def _header(lay: QVBoxLayout, title: str, text: str = "") -> None:
        lay.addWidget(QLabel(title, objectName="h1"))
        if text:
            lay.addWidget(QLabel(text, objectName="muted", wordWrap=True))
        lay.addSpacing(4)

    def _page_home(self) -> QWidget:
        page, lay = self._page()
        hero = QHBoxLayout()
        hero.setSpacing(18)
        self.btn_toggle = PowerButton()
        self.btn_toggle.setToolTip("Включить или выключить обход блокировок")
        self.btn_toggle.clicked.connect(self.toggle)
        hero.addWidget(self.btn_toggle, 0, Qt.AlignVCenter)
        col = QVBoxLayout()
        col.setSpacing(4)
        col.addStretch()
        self.status = QLabel(objectName="title")
        self.substatus = QLabel(objectName="sub", wordWrap=True)
        col.addWidget(self.status)
        col.addWidget(self.substatus)
        self.chips = QHBoxLayout()
        self.chips.setSpacing(6)
        self.chips.setContentsMargins(0, 4, 0, 0)
        col.addLayout(self.chips)
        col.addStretch()
        hero.addLayout(col, 1)
        acts = QVBoxLayout()
        acts.setSpacing(6)
        acts.addStretch()
        self.btn_check_access = QPushButton("Проверить доступ", objectName="tonal")
        self.btn_check_access.setToolTip("Проверить, открываются ли Discord, YouTube и голосовой чат прямо сейчас")
        self.btn_check_access.clicked.connect(lambda: self.check_access())
        self.btn_site = QPushButton("Сайт не открывается…")
        self.btn_site.setToolTip("Проверить конкретный сайт и, если поможет, добавить его в обход")
        self.btn_site.clicked.connect(self.check_site)
        self.btn_app = QPushButton("Программа не работает…", objectName="text")
        self.btn_app.setToolTip("Программа (Spotify, лаунчер, мод игры…) работает без обхода, а с обходом нет? "
                                "Укажите её exe — обход перестанет трогать её серверы")
        self.btn_app.clicked.connect(self.exclude_app)
        self.btn_cancel = QPushButton("Отменить подбор", objectName="text")
        self.btn_cancel.setToolTip("Остановить подбор; обход вернётся к прежней стратегии")
        self.btn_cancel.clicked.connect(self.cancel_busy)
        self.btn_cancel.hide()
        for b in (self.btn_check_access, self.btn_site, self.btn_app, self.btn_cancel):
            b.setCursor(Qt.PointingHandCursor)
            acts.addWidget(b)
        acts.addStretch()
        hero.addLayout(acts)
        lay.addLayout(hero)
        self.progress = QProgressBar(objectName="thin")
        self.progress.setTextVisible(False)
        self.progress.setRange(0, 0)
        self.progress.hide()
        lay.addWidget(self.progress)

        grid = QHBoxLayout()
        grid.setSpacing(10)
        card = QFrame(objectName="card")
        cl = QVBoxLayout(card)
        cl.setContentsMargins(14, 12, 14, 14)
        cl.setSpacing(8)
        cl.addWidget(QLabel("Стратегия", objectName="h2"))
        self.combo = QComboBox()
        self.combo.setToolTip("«Авто» — результат подбора. Можно выбрать стратегию вручную.")
        self.combo.addItem(AUTO)
        self.combo.addItems([s.name for s in strategies.load_all()])
        self.combo.activated.connect(self.on_combo)
        cl.addWidget(self.combo)
        self.btn_auto = QPushButton("Подобрать заново", objectName="primary")
        self.btn_auto.setCursor(Qt.PointingHandCursor)
        self.btn_auto.setToolTip("Проверить все стратегии на Discord, YouTube и голосе и собрать лучшую "
                                 "комбинацию. Занимает 2–4 минуты, обход на это время выключен.")
        self.btn_auto.clicked.connect(self.autopick)
        row = QHBoxLayout()
        row.addWidget(self.btn_auto)
        row.addStretch()
        cl.addLayout(row)
        cl.addStretch()
        grid.addWidget(card, 1)

        games = QFrame(objectName="card")
        gl = QVBoxLayout(games)
        gl.setContentsMargins(14, 12, 14, 14)
        gl.setSpacing(6)
        top = QHBoxLayout()
        top.addWidget(QLabel("Игры", objectName="h2"))
        top.addStretch()
        more = QPushButton("Все игры", objectName="text")
        more.setCursor(Qt.PointingHandCursor)
        more.clicked.connect(lambda: self.tabs.setCurrentWidget(self.page_games))
        top.addWidget(more)
        gl.addLayout(top)
        self.lbl_games = QLabel(objectName="muted", wordWrap=True, textFormat=Qt.RichText)
        gl.addWidget(self.lbl_games)
        gl.addStretch()
        grid.addWidget(games, 1)
        lay.addLayout(grid)

        recent = QFrame(objectName="card")
        rl = QVBoxLayout(recent)
        rl.setContentsMargins(14, 12, 14, 12)
        rl.setSpacing(4)
        rl.addWidget(QLabel("Последние события", objectName="h2"))
        self.lbl_recent = QLabel("Пока ничего не происходило.", objectName="muted", wordWrap=True)
        self.lbl_recent.setTextInteractionFlags(Qt.TextSelectableByMouse)
        rl.addWidget(self.lbl_recent)
        rl.addStretch()
        lay.addWidget(recent, 1)
        return page

    def _page_games(self) -> QWidget:
        page, lay = self._page()
        self._header(lay, "Игры", "Запущенные игры находятся сами: программа собирает их серверы и включает для них "
                                  "обход. Серверы входа и лаунчеров обход не трогает.")
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Игра", "Адресов", "Пинг, мс", "Сейчас"])
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.Stretch)
        for i in (1, 2, 3):
            hh.setSectionResizeMode(i, QHeaderView.ResizeToContents)
        hh.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.setShowGrid(False)
        self.table.verticalHeader().hide()
        self.games_empty = QLabel(objectName="empty", alignment=Qt.AlignCenter, wordWrap=True)
        lay.addWidget(self.games_empty, 1)
        lay.addWidget(self.table, 1)
        r = QHBoxLayout()
        r.setSpacing(8)
        b2 = QPushButton("Добавить игру…", objectName="tonal")
        b2.setToolTip("Выбрать exe игры вручную, если она не нашлась сама")
        b2.clicked.connect(self.add_game)
        b1 = self.btn_ignore = QPushButton("Не игра")
        b1.setToolTip("Больше не считать выбранную программу игрой и убрать её адреса из обхода")
        b1.clicked.connect(self.ignore_game)
        b3 = self.btn_reset = QPushButton("Сбросить адреса")
        b3.setToolTip("Убрать собранные адреса выбранной игры (если после добавления ей стало хуже)")
        b3.clicked.connect(self.reset_game)
        b4 = QPushButton("Поделиться адресами…", objectName="text")
        b4.setToolTip("Отправить найденные серверы игр в общую базу: у других игра заработает с первого запуска. "
                      "Отправляются только адреса серверов, без вашего IP. Нужен аккаунт GitHub.")
        b4.clicked.connect(self.share_games)
        for b in (b2, b1, b3):
            r.addWidget(b)
        for b in (b1, b3):  # act on the selected game: off until one is selected
            b.setEnabled(False)
        self.table.itemSelectionChanged.connect(
            lambda: [b.setEnabled(bool(self.table.selectedItems())) for b in (b1, b3)])
        r.addStretch()
        r.addWidget(b4)
        lay.addLayout(r)
        return page

    def _page_settings(self) -> QWidget:
        page, lay = self._page((20, 16, 20, 0))
        self._header(lay, "Настройки")
        area = QScrollArea(widgetResizable=True)
        body = QWidget(objectName="page")
        self.box_settings = body  # disabled while busy: a toggle would restart winws mid-pick
        bl = QVBoxLayout(body)
        bl.setContentsMargins(0, 0, 8, 24)
        bl.setSpacing(0)
        s = self.settings
        self.cb = {}
        groups = {}
        for sec, title in (("main", "Основное"), ("adv", "Дополнительно")):
            if sec == "adv":
                self.btn_adv = QPushButton(objectName="text")
                self.btn_adv.setCursor(Qt.PointingHandCursor)
                self.btn_adv.clicked.connect(lambda: self.set_advanced(not self.adv_box.isVisible()))
                bl.addWidget(self.btn_adv, 0, Qt.AlignLeft)
            box = QFrame(objectName="group")
            gl = QVBoxLayout(box)
            gl.setContentsMargins(4, 4, 4, 4)
            gl.setSpacing(0)
            if sec == "main":
                bl.addWidget(QLabel(title, objectName="section"))
            bl.addWidget(box)
            groups[sec] = gl
            if sec == "adv":
                self.adv_box = box
        for key, title, text, default, sec in self.SETTINGS:
            sw = Switch()
            sw.setChecked(bool(s.get(key, default)))
            sw.setAccessibleName(title)
            sw.setToolTip(text)  # short description on hover
            sw.toggled.connect(lambda v, k=key: self.on_check(k, v))
            self.cb[key] = sw
            groups[sec].addWidget(SettingRow(title, text, sw))
        self.ports = {}
        for proto, default in (("tcp", strategies.GAME_TCP), ("udp", strategies.GAME_UDP)):
            e = QLineEdit(s.get(f"game_ports_{proto}") or "", placeholderText=default)
            e.editingFinished.connect(lambda p=proto: self.on_ports(p))
            self.ports[proto] = e
            groups["adv"].addWidget(InputRow(
                f"Порты игр, {proto.upper()}",
                f"Какие порты {proto.upper()} обходить для игр (когда обход для игр включён). Через запятую, "
                f"диапазоны через дефис. Пусто — по умолчанию: {default}"
                + (" (1935 пропущен — трансляции OBS)." if proto == "tcp" else "."), e))
        bl.addWidget(QLabel("Обновления", objectName="section"))
        upd = QFrame(objectName="group")
        ul = QVBoxLayout(upd)
        ul.setContentsMargins(16, 14, 16, 16)
        ul.setSpacing(10)
        self.lbl_ver = QLabel(objectName="rowTitle")
        ul.addWidget(self.lbl_ver)
        self.lbl_upd = QLabel(objectName="muted", wordWrap=True)  # result of the last check
        ul.addWidget(self.lbl_upd)
        self.lbl_upd.hide()  # shown once there is a result: an empty line left a gap
        r = QHBoxLayout()
        r.setSpacing(8)
        self.btn_check = QPushButton("Проверить обновления", objectName="tonal")
        self.btn_check.setToolTip("Проверить, есть ли новые версии zapret и ZapretAuto")
        self.btn_check.clicked.connect(lambda: self.check_update(manual=True))
        self.btn_update = QPushButton("Обновить zapret")
        self.btn_update.setToolTip("Скачать и установить новую версию zapret")
        self.btn_update.clicked.connect(lambda: self.do_update(False))
        r.addWidget(self.btn_check)
        r.addWidget(self.btn_update)
        r.addStretch()
        ul.addLayout(r)
        bl.addWidget(upd)
        bl.addStretch(1)
        area.setWidget(body)
        lay.addWidget(area, 1)
        return page

    def _page_diag(self) -> QWidget:
        page, lay = self._page()
        self._header(lay, "Диагностика", "Поиск частых причин, по которым обход не работает: конфликтующие "
                                         "программы, зависший драйвер, подмена DNS и другое.")
        drow = QHBoxLayout()
        drow.setSpacing(8)
        self.btn_diag = QPushButton("Найти проблемы", objectName="primary")
        self.btn_diag.clicked.connect(self.run_diag)
        drow.addWidget(self.btn_diag)
        self.btn_report = QPushButton("Сохранить в zip…")
        self.btn_report.setToolTip("Собрать журнал, диагностику и настройки в zip, чтобы отправить разработчику. "
                                   "Имя пользователя Windows в отчёте скрыто.")
        self.btn_report.clicked.connect(self.save_report)
        drow.addWidget(self.btn_report)
        self.btn_send = QPushButton("Отправить отчёт разработчику…", objectName="tonal")
        self.btn_send.setToolTip("Не помогло? Откроется страница GitHub с готовым отчётом: версии, найденные "
                                 "проблемы и конец журнала. Имя пользователя и IP-адреса скрыты. Нужен аккаунт GitHub.")
        self.btn_send.clicked.connect(lambda: self.send_report())
        drow.addWidget(self.btn_send)
        drow.addStretch()
        lay.addLayout(drow)
        self.diag_table = QTableWidget(0, 3)
        self.diag_table.setHorizontalHeaderLabels(["Проверка", "Результат", ""])
        hh = self.diag_table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.Stretch)
        hh.setSectionResizeMode(2, QHeaderView.Fixed)
        hh.resizeSection(2, 124)  # room for «Исправить»: ResizeToContents ignored cell widgets and clipped it
        hh.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.diag_table.verticalHeader().hide()
        self.diag_table.verticalHeader().setMinimumSectionSize(40)  # fits the «Исправить» button
        self.diag_table.setShowGrid(False)
        self.diag_table.setWordWrap(True)
        self.diag_table.setEditTriggers(QTableWidget.NoEditTriggers)
        lay.addWidget(self.diag_table, 1)
        return page

    def _page_log(self) -> QWidget:
        page, lay = self._page()
        self._header(lay, "Журнал")
        stats = QFrame(objectName="card")
        sl = QVBoxLayout(stats)
        sl.setContentsMargins(20, 14, 20, 14)
        self.lbl_stats = QLabel(wordWrap=True, textFormat=Qt.RichText)
        sl.addWidget(self.lbl_stats)
        lay.addWidget(stats)
        self.logbox = QPlainTextEdit(readOnly=True)
        self.logbox.setMaximumBlockCount(5000)  # runs for weeks: keep the widget small
        lay.addWidget(self.logbox, 1)
        return page

    def _page_lists(self) -> QWidget:
        page, lay = self._page()
        self._header(lay, "Списки", "Ваши списки сайтов и адресов. Обход перечитывает их сам после сохранения.")
        self.list_combo = QComboBox()
        names = {"list-general-user.txt": "Сайты, для которых включить обход",
                 "list-exclude-user.txt": "Сайты, которые обход не трогает",
                 "ipset-exclude-user.txt": "IP-адреса, которые обход не трогает",
                 "games_manual": "Адреса игровых серверов (вручную)"}
        for key in lists.user_list_paths():
            self.list_combo.addItem(names.get(key, key), key)
        self.list_combo.addItem("Программы, которые обход не трогает (exe)", "apps_exclude")  # a setting, not a file
        self.list_combo.currentIndexChanged.connect(self.load_list)
        lay.addWidget(self.list_combo)
        self.list_edit = QPlainTextEdit(objectName="editor")
        lay.addWidget(self.list_edit, 1)
        r = QHBoxLayout()
        r.setSpacing(8)
        b = QPushButton("Сохранить", objectName="primary")
        b.clicked.connect(self.save_list)
        self.btn_ipset = QPushButton("Обновить IP-список с GitHub")
        self.btn_ipset.setToolTip("Скачать свежий список IP-диапазонов (Cloudflare и др.) для обхода")
        self.btn_ipset.clicked.connect(self.update_ipset)
        r.addWidget(b)
        r.addWidget(self.btn_ipset)
        r.addStretch()
        lay.addLayout(r)
        return page

    # ---------- diagnostics / lists ----------
    def run_diag(self):
        self.btn_diag.setEnabled(False)
        self.btn_diag.setText("Проверка...")
        self._diag_w = Worker(lambda log: diag.run_checks())
        self._workers.append(self._diag_w)
        self._diag_w.finished.connect(lambda w=self._diag_w: self._reap(w))
        self._diag_w.done.connect(self.bridge.diag_done.emit)
        self._diag_w.start()

    def on_diag_done(self, res):
        self.btn_diag.setEnabled(True)
        self.btn_diag.setText("Найти проблемы")
        self._last_diag = res
        t = self.diag_table
        t.setRowCount(0)
        from app.gui.style import C
        col = {"ok": C["good"], "warn": C["warn"], "err": C["error"]}
        order = {"err": 0, "warn": 1}
        for r in sorted(res or [], key=lambda r: order.get(r["level"], 2)):  # problems first
            i = t.rowCount()
            t.insertRow(i)
            a = QTableWidgetItem(("●  " if r["level"] != "ok" else "") + r["label"])
            a.setForeground(QColor(col.get(r["level"], C["outline"])))
            t.setItem(i, 0, a)
            t.setItem(i, 1, QTableWidgetItem(r["detail"]))
            if r.get("fix"):
                b = QPushButton("Исправить", objectName="tonal" if r["level"] != "ok" else "text")
                b.setCursor(Qt.PointingHandCursor)
                b.clicked.connect(lambda _=False, f=r["fix"]: self.apply_fix(f))
                cell = QWidget()  # centered, natural height: a bare cell widget stretched over a tall row
                cl = QHBoxLayout(cell)
                cl.setContentsMargins(4, 2, 4, 2)
                cl.addWidget(b, 0, Qt.AlignVCenter)
                t.setCellWidget(i, 2, cell)
        t.resizeRowsToContents()

    def apply_fix(self, fix):
        """Fixes may hit the network or kill processes: run them off the UI thread, then show the result."""
        self.btn_diag.setEnabled(False)

        def done(msg):
            msg = msg or "не удалось — подробности в журнале"
            self.log(f"исправление {fix}: {msg}")
            QMessageBox.information(self, "Диагностика", msg)
            self.run_diag()
        self._run(lambda log: diag.apply_fix(fix), done)

    def load_list(self, *_):
        if self.list_combo.currentData() == "apps_exclude":
            self.list_edit.setPlainText("".join(x + "\n" for x in self.settings.get("apps_exclude", [])))
            return
        p = lists.user_list_paths()[self.list_combo.currentData()]
        try:
            self.list_edit.setPlainText(p.read_text(encoding="utf-8"))
        except OSError:
            self.list_edit.setPlainText("")

    def save_list(self):
        if self.list_combo.currentData() == "apps_exclude":
            apps = sorted({ln.strip().lower() for ln in self.list_edit.toPlainText().splitlines()
                           if ln.strip().lower().endswith(".exe")})
            self.save(apps_exclude=apps)
            self.log("сохранён список программ, которые обход не трогает")
            return
        p = lists.user_list_paths()[self.list_combo.currentData()]
        p.parent.mkdir(parents=True, exist_ok=True)
        text = self.list_edit.toPlainText()
        if not any(l.strip() and not l.strip().startswith("#") for l in text.splitlines()):
            # winws refuses empty lists (and re-reads them live): keep a harmless placeholder entry
            text = text.rstrip() + ("\n" if text.strip() else "") + (
                "203.0.113.113/32" if p.name.startswith("ipset") else "domain.example.abc") + "\n"
            self.list_edit.setPlainText(text)
        from app import paths
        paths.write_list(p, text)
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
        a7 = QAction("Не работает? Отправить отчёт…", m)
        a7.triggered.connect(lambda: (self.show_window(), self.send_report()))
        for a in (self.act_toggle, a6, a5, a2, a3, a7, a4):
            m.addAction(a)
        self._menu = m
        self.tray.setContextMenu(m)
        self.tray.activated.connect(lambda r: self.show_window() if r in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick) else None)
        self.tray.show()

    # ---------- helpers ----------
    def log(self, text: str):
        self.logbox.appendPlainText(time.strftime("%H:%M:%S ") + text)
        if not text.startswith(("gamewatch:", "zapret2: скачиваю")):  # home card: events for people, not internals
            self._recent = (getattr(self, "_recent", []) + [time.strftime("%H:%M ") + text])[-8:]
            self.lbl_recent.setText("\n".join(self._recent))
        logs.log.info(text)

    def showEvent(self, e):
        super().showEvent(e)
        self._title_bar()

    def _title_bar(self):
        """Dark or light window title bar to match the theme."""
        try:
            import ctypes
            hwnd, on = int(self.winId()), ctypes.c_int(0 if self._light else 1)
            for attr in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE (new / pre-20H1 builds)
                if ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(on), 4) == 0:
                    break
        except Exception:  # noqa: BLE001
            pass

    def apply_theme(self, force: bool = False):
        """Follow the Windows light/dark setting (checked at start and every ~10 s)."""
        light = style.windows_light()
        if style.set_theme(light) or force:
            self._light = light
            QApplication.instance().setStyleSheet(style.build_style())
            self._title_bar()
            self.update()
            for w in self.findChildren(QWidget):
                w.update()  # custom-painted widgets read the tokens on paint
            if hasattr(self, "diag_table"):  # colors baked into items / rich text: refill them
                if getattr(self, "_last_diag", None):
                    self.on_diag_done(self._last_diag)
                self.fill_games()
                self.fill_stats()

    def set_chips(self, res: dict):
        """Service status chips after an access check: a dot and the name, and the words when it fails."""
        while self.chips.count():
            it = self.chips.takeAt(0)
            if it.widget():
                it.widget().hide()  # deleteLater waits for the event loop: the old chip showed under the new
                it.widget().deleteLater()
        res = dict(res)
        speed = res.pop("Скорость YouTube", None)  # one YouTube chip: "медленно" when it opens but crawls
        for name, ok in res.items():
            slow = name == "YouTube" and ok and speed is False
            self.chips.addWidget(StatusChip(name, ok and not slow, "медленно" if slow else ""))
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
        if self._zapret2_on():
            from app import zapret2
            return zapret2.NAME
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
        elif not self.busy:
            self.substatus.setText(f"Стратегия: {self.describe()}" if run
                                   else "Нажмите кнопку, чтобы включить. Стратегия: " + self.describe())
        err = "yes" if self.error and not self.busy else "no"
        if self.substatus.property("error") != err:
            self.substatus.setProperty("error", err)
            self.substatus.style().unpolish(self.substatus)
            self.substatus.style().polish(self.substatus)
        last = getattr(self, "_last_check", None)
        tip = f"ZapretAuto — {title}\n{self.describe()}"
        if last:
            ok = "всё работает" if last[1] else "есть сбои"
            tip += f"\nпроверка {time.strftime('%H:%M', time.localtime(last[0]))}: {ok}"
        self.tray.setToolTip(tip[:127])  # Windows truncates tray tooltips at 127 chars
        self.btn_toggle.setAccessibleName(title)
        for w in (self.btn_toggle, self.combo, self.box_settings, self.btn_check_access, self.btn_site):
            w.setEnabled(not self.busy)
        if self._zapret2_on():  # zapret2 picks strategies per site itself: the winws list does not apply
            self.combo.setEnabled(False)
        self.btn_toggle.set_state(state)
        self.act_toggle.setText("Выключить" if run else "Включить")
        self.btn_auto.setEnabled(not self.busy)
        c = self.settings.get("strategy")
        manual = c and not c.get("picks") and c != self._auto_choice()
        self.combo.setCurrentIndex(max(0, self.combo.findText(c["base"])) if manual else 0)

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
            flags = self._game_flags()
            self.runner.start(s, *flags, self.settings.get("auto_hostlist", True))
            self._run_flags = flags
            self.log(f"запущено: {s.name}")
            stats.record("start")
            self.error = ""
            self._expect_running = True
        except Exception as e:  # noqa: BLE001
            self.log(f"ошибка запуска: {e}")
            self.error = "Не удалось запустить обход. Откройте раздел «Диагностика» → «Найти проблемы»."
            self._explain_failure()
        self.refresh()
        return self.runner.running

    def send_report(self, what: str | None = None):
        """Publish the problem as a GitHub issue (the user confirms it on the page)."""
        if what is None:
            what, ok = QInputDialog.getText(self, "Отправить отчёт",
                                            "Коротко: что не работает? (например: Discord не грузится)")
            if not ok:
                return
        import webbrowser
        url = logs.issue_url(what, getattr(self, "_last_diag", None))
        webbrowser.open(url)
        self.log("открыта страница отправки отчёта на GitHub")

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
        rows = [("Обход работал", f"{s['uptime_h']} ч"),
                ("Фоновых проверок", f"{s['checks']}, успешных {pct}" if s["checks"] else "ещё не было"),
                ("Сам чинил обход", f"{s['repicks']} раз, последний: {last}"),
                ("Неожиданных остановок обхода", str(s["crashes"])),
                ("Обновлений IP-списка", str(s["ipset"]))]
        if s["switches"]:
            rows.append(("Смены стратегии", ", ".join(f"{time.strftime('%d.%m %H:%M', time.localtime(t))} — {n}"
                                                      for t, n in s["switches"][-3:])))
        muted = style.C["on_surface_variant"]
        self.lbl_stats.setText(
            f"<div style='font-weight:600; margin-bottom:6px'>За последние {s['days']} дней</div>"
            "<table cellspacing='0' cellpadding='3'>"
            + "".join(f"<tr><td style='color:{muted}; padding-right:18px'>{k}</td><td>{v}</td></tr>" for k, v in rows)
            + "</table>")

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
            import socket
            try:
                socket.gethostbyname(dom)
            except socket.gaierror:
                from app import diag
                try:
                    exists = bool(diag._doh(dom))
                except OSError:
                    exists = False
                if exists:  # ISPs answer "no such domain" for blocked sites: the bypass can't fix DNS
                    return (f"Провайдер не даёт найти {dom}: DNS отвечает, что такого сайта нет, хотя он есть. "
                            "Включите защищённый DNS: «Диагностика» → «Найти проблемы» → «Подмена DNS» → «Исправить».")
                return f"Сайт {dom} не найден — проверьте адрес."
            before = tester.probe(t) is not None
            if before:
                return f"{dom} уже открывается — обход для него не нужен."
            added = lists.add_user_domain(dom)
            time.sleep(1.0)  # winws re-reads the changed list by itself (on the next packet): no restart
            if any(tester.probe(t) is not None for _ in range(3)):  # first tries through DPI can be reset
                return f"Готово: {dom} добавлен в обход и открывается."
            if added:
                lists.remove_user_domain(dom)
            # the other way round: the bypass itself may break it (Cloudflare/Amazon ranges in the IP list,
            # sign-in pages, launchers) — try with the site excluded from every profile
            excluded = lists.add_user_domain(dom, "list-exclude-user.txt")
            time.sleep(1.0)
            if any(tester.probe(t) is not None for _ in range(2)):
                return (f"Готово: {dom} ломался из-за обхода — теперь обход его не трогает, и сайт открывается. "
                        "Отменить: «Списки» → «Сайты, которые обход не трогает».")
            if excluded:
                lists.remove_user_domain(dom, "list-exclude-user.txt")
            ip_ok = tester.probe(tester.Target("ip", f"tcp://{dom}:443")) is not None
            if not ip_ok:
                return f"{dom} заблокирован по IP-адресу — zapret тут не поможет, нужен VPN. [ip]"
            return f"Не помогло для {dom}. Попробуйте «Подобрать заново» на главной."

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
            if self._health_fails >= 2 and self._zapret2_on():
                self._health_fails = 0  # a pick would test winws strategies: not what runs now
                self.log("режим zapret2: стратегии для сайтов переключаются сами, автоподбор не запускаю")
                return
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
        if (updater and self.settings.get("auto_update", True) and not self.busy
                and now >= getattr(self, "_next_update_check", now + 1) and not self._game_running()):
            self._next_update_check = now + UPDATE_EVERY  # the app runs for weeks: don't update only at start
            self._auto_check = True
            self.check_update()
        if self._ticks % 5 == 0:
            self.apply_theme()  # Windows light/dark switched: follow it live
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
                self.error = "Обход постоянно падает. Проверьте антивирус и раздел «Диагностика»."
                self.log("winws падает слишком часто — автоперезапуск остановлен")
                self.tray.showMessage("ZapretAuto", self.error)
                self._explain_failure()
        if self._repick_pending and not self.busy and not self._game_running():
            self._repick_pending = False
            self.log("игра закрыта — чиню обход")
            self.autopick()
        net = tester.egress_ip() if int(now) % 10 < 2 else self._net  # ~every 10 s; psutil-free (it leaked handles)
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
        if self.isVisible() and self.tabs.currentIndex() in (0, self.tabs.indexOf(self.page_games)):
            self.fill_games()  # live ping / «играете» on the games page and the home card
        if now >= self._next_health:
            self._next_health = now + 600
            self._health()
        self.refresh()

    def _reap(self, w):
        """A worker finished: join its thread (frees the OS thread handle) and drop the QObject — the signal
        connections form a reference cycle that otherwise kept both alive (~2 handles per job, every minute)."""
        if w in self._workers:
            self._workers.remove(w)
        w.wait(1000)
        w.deleteLater()

    def _run(self, fn, done, line=None):
        """Start a Worker and keep a reference until its thread has finished."""
        w = Worker(fn)
        w.line.connect(line or self.log)
        w.done.connect(done)
        w.finished.connect(lambda: self._reap(w))
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

    def _auto_choice(self) -> dict | None:
        """Result of the last auto-pick (a single strategy or a mix). Older versions did not store it: then the
        current strategy counts as auto when it came from a pick (last_pick present) or is a mix."""
        c = self.settings.get("auto_choice")
        if c:
            return c
        cur = self.settings.get("strategy")
        return cur if cur and (self.settings.get("last_pick") or cur.get("picks")) else None

    def on_combo(self, i):
        if i == 0:
            auto = self._auto_choice()
            if not auto:
                QMessageBox.information(self, "ZapretAuto", "Стратегия ещё не подбиралась. "
                                        "Нажмите «Подобрать лучшую стратегию».")
                self.refresh()
                return
            self.save(strategy=auto)
        else:
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
        elif key == "zapret2":
            self.set_zapret2(v)
        elif key in ("game_tcp", "game_udp", "auto_hostlist") and self.runner.running and not self.busy:
            self.start_runner()

    def _zapret2_on(self) -> bool:
        """zapret2 mode is what runs (or would run): enabled and its files are in place."""
        if not self.settings.get("zapret2"):
            return False
        from app import zapret2
        return zapret2.installed()

    def on_ports(self, proto: str):
        """Save the game filter ports; a bad value is shown in red and not applied."""
        e = self.ports[proto]
        text = e.text().replace(" ", "")
        ok = not text or strategies.valid_ports(text)
        e.setProperty("error", "no" if ok else "yes")
        e.style().unpolish(e)
        e.style().polish(e)
        key = f"game_ports_{proto}"
        if not ok or text == (self.settings.get(key) or ""):
            return
        self.save(**{key: text})
        self.log(f"порты игр {proto.upper()}: {text or 'по умолчанию'}")
        if self.runner.running and not self.busy and self._game_flags()[0 if proto == "tcp" else 1]:
            self.start_runner()  # the filter ports are fixed at start

    def set_zapret2(self, on: bool):
        """Switch the engine: download winws2 on first use, then restart the bypass with it (or back)."""
        from app import zapret2

        def switched(ok):
            if not ok:
                self.error = "Не удалось скачать zapret2 — подробности в журнале."
                self.cb["zapret2"].blockSignals(True)
                self.cb["zapret2"].setChecked(False)
                self.cb["zapret2"].blockSignals(False)
                self.save(zapret2=False)
            self.log("режим zapret2 " + ("включён: стратегии переключаются сами для каждого сайта" if on and ok
                                         else "выключен"))
            if self.runner.running or self._expect_running:
                self.start_runner()
            self.refresh()
        if on and not zapret2.installed():
            self._run(lambda log: zapret2.install(log), switched)
        else:
            switched(True)

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
        if self._zapret2_on():  # the pick tests winws strategies: it would not change anything in zapret2 mode
            self.log("режим zapret2: стратегии переключаются сами, подбор не нужен")
            if self.isVisible():
                QMessageBox.information(self, "ZapretAuto", "Включён режим zapret2: он сам переключает стратегии для "
                                        "каждого сайта, подбор не нужен. Чтобы подобрать стратегию обычным способом, "
                                        "выключите «zapret2 (эксперимент)» в разделе «Дополнительно».")
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
        self._run(lambda log: tester.auto_pick(log, stop=lambda: self._cancel), self._autopick_done, line)

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
            self.error = "Подбор не удался — подробности в разделе «Журнал»."
        if res or self.settings.get("strategy"):
            self.start_runner()  # the pick stopped the bypass: always bring it back (old strategy if pick failed)
        if getattr(self, "_repair", False):  # this pick was an automatic repair: tell how it ended
            self._repair = False
            self.tray.showMessage("ZapretAuto", f"Обход починен: {self.describe()}" if res else
                                  "Починить автоматически не удалось — откройте окно программы")
        if res:
            self._pick_summary()
            self._offer_autostart()
            self._offer_share()
        elif not cancelled and self.isVisible() and QMessageBox.question(
                self, "ZapretAuto", "Подбор не помог. Отправить отчёт разработчику?\n\nОткроется страница GitHub "
                "с готовым отчётом (версии и конец журнала, имя пользователя и IP скрыты). Нужен аккаунт GitHub.") \
                == QMessageBox.Yes:
            self.send_report()
        self.refresh()

    def _pick_summary(self):
        """What the pick achieved, in one line (log + tray), and the service chips from its final runs."""
        lp = self.settings.get("last_pick") or {}
        if not lp.get("total") or time.time() - lp.get("time", 0) > 600:
            return
        line = f"Готово: {self.describe()} — проходит {lp['score']} из {lp['total']} проверок"
        if lp.get("before") is not None:
            line += f" (было {lp['before']})"
        if lp.get("services"):
            self.set_chips(lp["services"])
            bad = [k for k, v in lp["services"].items() if not v and k != "Скорость YouTube"]
            if bad:
                line += ". Не открывается: " + ", ".join(bad)
        self.log(line)
        self.tray.showMessage("ZapretAuto", line)

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
        self.fill_games()  # known games show at once: the table used to fill only when a game got new IPs

    def stop_watcher(self):
        if self.watcher:
            self.watcher.stop()
            self.watcher = None
            self.log("слежение за играми выключено")
            self.fill_games()

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
            for j, t in enumerate([gamewatch.display_name(exe, g.get("path")), str(len(g.get("ips", ()))),
                                   "—" if ping is None else f"{ping:.0f}", "играете" if g.get("active") else ""]):
                it = QTableWidgetItem(t)
                if j == 0:
                    it.setData(Qt.UserRole, exe)  # the exe is the key; the name is for people
                    it.setToolTip(exe)
                self.table.setItem(i, j, it)
        self._fill_home_games(rows)

    def _fill_home_games(self, rows: list) -> None:
        """Home card: running games first, then the ones with most servers; three at most."""
        if not self.settings.get("game_watch"):
            self.lbl_games.setText("Слежение за играми выключено — включите его в «Настройках».")
            return
        if not rows:
            self.lbl_games.setText("Пока игр не найдено. Запустите игру — она появится здесь сама.")
            return
        top = sorted(rows, key=lambda r: (not r[1].get("active"), -len(r[1].get("ips", ()))))[:3]
        lines = []
        for exe, g in top:
            name = gamewatch.display_name(exe, g.get("path"))
            ping = g.get("ping_ms")
            state = (f"<span style='color:{style.C["good"]}'>играете</span>" + (f", {ping:.0f} мс" if ping else "")
                     if g.get("active") else f"{len(g.get('ips', ()))} адресов в обходе")
            lines.append(f"<span style='color:{style.C["on_surface"]}'>{name}</span> — {state}")
        more = len(rows) - len(top)
        self.lbl_games.setText("<br>".join(lines) + (f"<br>и ещё {more}" if more > 0 else ""))

    def _selected_game(self) -> str | None:
        r = self.table.currentRow()
        if r < 0 or not self.table.item(r, 0):
            QMessageBox.information(self, "ZapretAuto", "Сначала выберите игру в списке.")
            return None
        return self.table.item(r, 0).data(Qt.UserRole)

    def ignore_game(self):
        exe = self._selected_game()
        if not exe:
            return
        self.save(games_ignore=sorted(set(self.settings.get("games_ignore", [])) | {exe}))
        if self.watcher:  # not a game: its servers must leave the game ipset (and the game filter)
            self.watcher.reset_game(exe)
        self.fill_games()

    def reset_game(self):
        exe = self._selected_game()
        if not exe or not self.watcher:
            return
        self.watcher.reset_game(exe)
        self.log(f"IP игры сброшены: {exe}")
        self.fill_games()

    def exclude_app(self):
        """A program broken by the bypass: its web servers (80/443) leave the IP-list profiles, as launchers'.
        The watcher learns them from its traffic, so watching turns on."""
        path, _ = QFileDialog.getOpenFileName(self, "Какая программа не работает с обходом?", "", "Программы (*.exe)")
        if not path:
            return
        exe = path.replace("/", "\\").rsplit("\\", 1)[-1].lower()
        self.save(apps_exclude=sorted(set(self.settings.get("apps_exclude", [])) | {exe}), game_watch=True)
        if not self.watcher:
            self.cb["game_watch"].blockSignals(True)
            self.cb["game_watch"].setChecked(True)
            self.cb["game_watch"].blockSignals(False)
            self.start_watcher()
        self.log(f"обход не будет трогать серверы {exe}")
        QMessageBox.information(self, "ZapretAuto", f"Готово: обход перестанет трогать серверы {exe}.\n\n"
                                "Перезапустите программу — её адреса соберутся сами за пару секунд, и она "
                                "должна заработать. Отменить: «Списки» → «Программы, которые обход не трогает».")

    def add_game(self):
        path, _ = QFileDialog.getOpenFileName(self, "Выберите exe игры", "", "Программы (*.exe)")
        if path:
            exe = path.replace("/", "\\")
            name = exe.rsplit("\\", 1)[-1].lower()
            ignored = name in {x.lower() for x in self.settings.get("games_ignore", [])}
            if self.watcher and name in self.watcher.games() and not ignored:
                rows = [self.table.item(i, 0).data(Qt.UserRole) for i in range(self.table.rowCount())]
                if name in rows:
                    self.table.selectRow(rows.index(name))
                QMessageBox.information(self, "ZapretAuto", f"{name} уже есть в списке игр.")
                return
            self.save(games_force=sorted(set(self.settings.get("games_force", [])) | {exe}),
                      games_ignore=[x for x in self.settings.get("games_ignore", []) if x.lower() != name],
                      game_watch=True)  # adding a game by hand means "watch it": also undo an earlier «Не игра»
            if not self.watcher:
                self.cb["game_watch"].blockSignals(True)
                self.cb["game_watch"].setChecked(True)
                self.cb["game_watch"].blockSignals(False)
                self.start_watcher()
            if self.watcher:
                self.watcher.add_manual(exe)
            self.log(f"игра добавлена: {exe}")
            self.fill_games()

    def share_games(self):
        games = self.watcher.games() if self.watcher else {}
        ign = set(self.settings.get("games_ignore", []))
        found = {exe: sorted(g["ips"]) for exe, g in games.items() if g.get("ips") and exe not in ign}
        if not found:
            QMessageBox.information(self, "ZapretAuto", "Пока нечем делиться: адреса появятся, когда вы поиграете.")
            return
        text = ("Отправить адреса серверов игр в общую базу?\n\n" + ", ".join(sorted(found)) + "\n\n"
                "У других пользователей эти игры заработают с первого запуска. Отправляются только адреса "
                "серверов игр — без вашего IP. Откроется страница GitHub (нужен аккаунт), там нажмите «Create».")
        if QMessageBox.question(self, "Поделиться адресами игр", text) != QMessageBox.Yes:
            return
        import webbrowser
        from app import __version__, sharedb
        webbrowser.open(sharedb.games_report_url(__version__, found, self.watcher.web_nets()))
        self.log("открыта страница отправки адресов игр")

    # ---------- updates ----------
    def check_update(self, manual: bool = False):
        if updater:
            self.log("проверка обновлений…")
            self._upd = {}
            if manual:  # a click only checks; an auto check still in flight must not start an update now
                self._auto_check = False
                self.lbl_upd.show()
                self.lbl_upd.setText("Проверяю…")
                self.btn_check.setEnabled(False)
            updater.check_async(lambda tag, newer: self.bridge.update_checked.emit(tag, newer))
            if hasattr(updater, "app_check_async"):
                updater.app_check_async(lambda tag, newer: self.bridge.app_checked.emit(tag, newer))

    def _show_upd(self, key, text):
        self._upd = {**getattr(self, "_upd", {}), key: text}
        if len(self._upd) == 2:  # both answers in (zapret + app)
            self.lbl_upd.show()
            self.lbl_upd.setText(f"{self._upd['app']}. {self._upd['zapret']}. Проверено в {time.strftime('%H:%M')}.")
            self.btn_check.setEnabled(True)

    def on_app_checked(self, tag, newer):
        from app import __version__
        auto = self.settings.get("auto_update", True)
        self._show_upd("app", f"ZapretAuto {__version__}: " + (
            f"не удалось проверить ({getattr(updater, 'APP_CHECK_ERROR', '') or 'нет связи с GitHub'})" if not tag else
            f"найдена версия {tag}" + (", устанавливаю" if auto else "") if newer else "последняя версия"))
        if not newer or getattr(self, "_app_updating", False):
            return
        self.log(f"доступна новая версия ZapretAuto: {tag}")
        if self.settings.get("auto_update", True) or QMessageBox.question(
                self, "ZapretAuto", f"Установить ZapretAuto {tag}?") == QMessageBox.Yes:
            def done(ok):
                self._app_updating = False
                if ok is True:  # installer replaces files and restarts the app
                    self.tray.showMessage("ZapretAuto", f"Устанавливаю версию {tag}, программа перезапустится…")
                    QTimer.singleShot(1500, self.quit)
                else:
                    self.log("самообновление не выполнено")
                    self.lbl_upd.show()
                    self.lbl_upd.setText(f"Не удалось установить ZapretAuto {tag} — подробности в журнале.")
            self._app_updating = True  # two checks at once downloaded and ran the installer twice
            self._run(lambda log: updater.app_update(log), done)

    def on_update_checked(self, tag, newer):
        self.log(f"zapret {tag}: " + ("есть обновление" if newer else "последняя версия") if tag else "zapret: проверить обновления не удалось")
        self._show_upd("zapret", "zapret: " + ("не удалось проверить" if not tag else
                                              f"есть версия {tag} — нажмите «Обновить»" if newer and not self._auto_check
                                              else f"обновляю до {tag}" if newer else f"{tag}, последняя версия"))
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
        self._run(lambda log: updater.update(log, self.runner.stop),
                  lambda ok: self._update_done(ok, was, auto))

    def _update_done(self, ok, was, auto):
        self.busy = False
        self.log("обновление завершено" if ok else "обновление не выполнено")
        if ok and (was or auto):
            self._verify_after_update()
            return
        if was:
            self.start_runner()
        self.refresh()

    def _verify_after_update(self):
        """New zapret: keep the strategy if Discord/YouTube still open with it; re-pick (2–4 min offline) only if not."""
        if not tester.chosen_strategy() or not self.start_runner():
            self.log("стратегии после обновления нет — подбираю заново")
            self.autopick()
            return
        self.busy, self.busy_text = True, "Проверяю обход после обновления…"
        self.refresh()

        def done(res):
            self.busy = False
            core = [v for k, v in (res or {}).items() if k in ("Discord", "YouTube")]
            if res and not all(core):
                self.log("после обновления не работает: " + ", ".join(k for k, v in res.items() if not v)
                         + " — подбираю стратегию заново")
                self.autopick()
                return
            self.log("после обновления всё работает — стратегия оставлена" if res else
                     "проверить после обновления не удалось — стратегия оставлена")
            self.refresh()
        self._run(lambda log: tester.quick_check(light=True), done)

    # ---------- lifecycle ----------
    def startup(self, autostart=True):
        if self.settings.get("autostart"):
            self.set_autostart(True)  # refresh the task (adds --tray for older installs)
        if not autostart:
            self.log("ZAPRET_NO_AUTOSTART: автозапуск отключён")
            return
        self._next_update_check = time.time() + UPDATE_EVERY
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
        for w in list(self._workers):  # let running jobs (downloads, picks) end before the process exits
            w.wait(3000)
        QApplication.quit()
