"""Страницы окна: главная, игры, списки, диагностика, журнал, настройки."""
import html
import os
import re
import time
from typing import TYPE_CHECKING

from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from app import paths
from app.gui.shared import (
    AUTO,
    COLORS,
    InputRow,
    Pages,
    PowerButton,
    Rail,
    SettingRow,
    StatusChip,
    Switch,
    TextLine,
    app_icon,
    gamewatch,
    init_fonts,
    lists,
    q,
    stats,
    strategies,
    style,
    tester,
    updater,
)

from .window_base import WindowBase

if TYPE_CHECKING:
    pass

# Что за группа целей у профиля в комбинации: ключ приходит из tester.target_group (или target.group в списках)
GROUP_NAMES = {
    "tcp:443:list-google": "Google",
    "tcp:80,443:list-general": "сайты",
    "tcp:80,443,8443:ipset-all": "IP-список",
    "udp:443:list-general": "QUIC",
    "udp:443:ipset-all": "QUIC IP-список",
    "udp:19294-19344,50000-50100:discord,stun": "голос Discord",
    "udp:other": "STUN",
}

# Режимы DNS: «provider» и «auto» - особые, остальные совпадают с именами в app.dns.RESOLVERS
DNS_MODES = (
    ("От провайдера", "provider"),
    ("Авто (лучший)", "auto"),
    ("Cloudflare", "Cloudflare"),
    ("Google", "Google"),
    ("Quad9", "Quad9"),
    ("AdGuard", "AdGuard"),
    ("Яндекс (Basic)", "Яндекс"),
    ("Свой", "custom"),
)

def group_name(key: str) -> str:
    """Подпись группы для человека; незнакомую группу показываем как есть, а не теряем."""
    return GROUP_NAMES.get(key, key)

def short_name(name: str) -> str:
    """«FAKE TLS AUTO ALT» → «FTAA»: в строке статуса важна компактность, полное имя - в подсказке.
    Имена без пробелов (ALT8, EXP) остаются как есть, цифры в конце сохраняются (ALT 2 → ALT2)."""
    words = re.findall(r"[A-Za-zА-Яа-я0-9]+", name)
    if len(words) <= 1:
        return name
    alpha = [w for w in words if not w.isdigit()]
    digits = "".join(w for w in words if w.isdigit())
    tail = re.search(r"(\d+)$", words[-1])
    if tail and not words[-1].isdigit():  # «FAKE TLS AUTO ALT2» → FTAA2: варианты не должны сливаться
        digits += tail.group(1)
    if len(alpha) == 1:  # «ALT 2» → «ALT2», а не «A2»
        return (alpha[0] + digits).upper()
    return ("".join(w[0] for w in alpha) + digits).upper()

class PagesMixin(WindowBase):
    def _build(self):
        app = QApplication.instance()
        if isinstance(app, QApplication):
            app.setStyle("Fusion")
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
            shortcut = QShortcut(QKeySequence(f"Ctrl+{i + 1}"), self)
            shortcut.activated.connect(lambda i=i: self.tabs.setCurrentIndex(i)
                                       if not self.tabs.rail.buttons[i].isHidden() else None)
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
        w = q(QWidget(), objectName="page")
        lay = QVBoxLayout(w)
        lay.setContentsMargins(*margins)
        lay.setSpacing(10)
        return w, lay

    @staticmethod
    def _header(lay: QVBoxLayout, title: str, text: str = "") -> None:
        lay.addWidget(q(QLabel(title), objectName="h1"))
        if text:
            lay.addWidget(q(QLabel(text), objectName="muted", wordWrap=True))
        lay.addSpacing(4)

    def _page_home(self) -> QWidget:
        page, lay = self._page()
        hero = QHBoxLayout()
        hero.setSpacing(18)
        self.btn_toggle = PowerButton()
        self.btn_toggle.setToolTip("Включить или выключить обход блокировок")
        self.btn_toggle.clicked.connect(self.toggle)
        hero.addWidget(self.btn_toggle, 0, Qt.AlignmentFlag.AlignVCenter)
        col = QVBoxLayout()
        col.setSpacing(4)
        col.addStretch()
        # имя стратегии и текст ошибки приходят из файлов: HTML в них не разметка, а данные
        self.status = q(QLabel(), objectName="title", textFormat=Qt.TextFormat.PlainText)
        self.substatus = q(TextLine(), objectName="sub", wordWrap=True, textFormat=Qt.TextFormat.PlainText)
        col.addWidget(self.status)
        srow = QHBoxLayout()
        srow.setSpacing(6)
        srow.setContentsMargins(0, 0, 0, 0)
        srow.addWidget(self.substatus, 0, Qt.AlignmentFlag.AlignBottom)
        # «?» рядом с комбинацией: подсказка при наведении и по нажатию, без отдельного окна
        self.btn_desc_help = QToolButton()
        self.btn_desc_help.setText("?")
        self.btn_desc_help.setAutoRaise(True)
        self.btn_desc_help.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_desc_help.setFixedSize(20, 20)
        self.btn_desc_help.setAccessibleName("Стратегии в комбинации")
        self.btn_desc_help.clicked.connect(self.show_strategy_help)
        srow.addWidget(self.btn_desc_help, 0, Qt.AlignmentFlag.AlignBottom)
        srow.addStretch(1)
        col.addLayout(srow)
        self.chips = QHBoxLayout()
        self.chips.setSpacing(6)
        self.chips.setContentsMargins(0, 0, 0, 0)
        col.addStretch()
        hero.addLayout(col, 1)
        acts = QVBoxLayout()
        acts.setSpacing(6)
        acts.addStretch()
        self.btn_check_access = q(QPushButton("Проверить доступ"), objectName="tonal")
        self.btn_check_access.setToolTip("Проверить, открываются ли Discord, YouTube, голосовой чат и Telegram "
                                         "прямо сейчас")
        self.btn_check_access.clicked.connect(lambda: self.check_access())
        self.btn_site = QPushButton("Сайт не открывается…")
        self.btn_site.setToolTip("Проверить конкретный сайт и, если поможет, добавить его в обход")
        self.btn_site.clicked.connect(self.check_site)
        self.btn_app = q(QPushButton("Программа не работает…"), objectName="text")
        self.btn_app.setToolTip("Программа (Spotify, лаунчер, мод игры…) работает без обхода, а с обходом нет? "
                                "Укажите её exe, обход перестанет трогать её серверы")
        self.btn_app.clicked.connect(self.exclude_app)
        self.btn_cancel = q(QPushButton("Отменить подбор"), objectName="text")
        self.btn_cancel.setToolTip("Остановить подбор; обход вернётся к прежней стратегии")
        self.btn_cancel.clicked.connect(self.cancel_busy)
        self.btn_cancel.hide()
        for b in (self.btn_check_access, self.btn_site, self.btn_app, self.btn_cancel):
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            acts.addWidget(b)
        acts.addStretch()
        hero.addLayout(acts)
        lay.addLayout(hero)
        lay.addLayout(self.chips)  # own full-width row: next to the action buttons they got squeezed and clipped
        self.progress = q(QProgressBar(), objectName="thin")
        self.progress.setTextVisible(False)
        self.progress.setRange(0, 0)
        self.progress.hide()
        lay.addWidget(self.progress)
        grid = QHBoxLayout()
        grid.setSpacing(10)
        card = q(QFrame(), objectName="card")
        card.setMinimumHeight(170)
        cl = QVBoxLayout(card)
        cl.setContentsMargins(14, 12, 14, 14)
        cl.setSpacing(8)
        cl.addWidget(q(QLabel("Стратегия"), objectName="h2"))
        self.combo = QComboBox()
        self.combo.setToolTip("«Авто», результат подбора. Можно выбрать стратегию вручную.")
        self.combo.addItem(AUTO)
        self.combo.addItems([s.name for s in strategies.load_all()])
        self.combo.activated.connect(self.on_combo)
        cl.addWidget(self.combo)
        # провайдер приходит из ipinfo.io, это данные, а не разметка
        self.lbl_isp = q(QLabel(), objectName="muted", wordWrap=True, textFormat=Qt.TextFormat.PlainText)  # ISP + shared base
        self.lbl_isp.hide()
        cl.addWidget(self.lbl_isp)
        self.btn_auto = q(QPushButton("Подобрать заново"), objectName="primary")
        self.btn_auto.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_auto.setToolTip("Проверить все стратегии на Discord, YouTube и голосе и собрать лучшую "
                                 "комбинацию. Занимает 2-4 минуты, обход на это время выключен.")
        self.btn_auto.clicked.connect(self.autopick)
        row = QHBoxLayout()
        row.addWidget(self.btn_auto)
        row.addStretch()
        cl.addLayout(row)
        cl.addStretch()
        grid.addWidget(card, 1)
        games = q(QFrame(), objectName="card")
        games.setMinimumHeight(170)
        gl = QVBoxLayout(games)
        gl.setContentsMargins(14, 12, 14, 14)
        gl.setSpacing(6)
        top = QHBoxLayout()
        top.addWidget(q(QLabel("Игры"), objectName="h2"))
        top.addStretch()
        more = q(QPushButton("Все игры"), objectName="text")
        more.setCursor(Qt.CursorShape.PointingHandCursor)
        more.clicked.connect(lambda: self.tabs.setCurrentWidget(self.page_games))
        top.addWidget(more)
        gl.addLayout(top)
        self.lbl_games = q(QLabel(), objectName="muted", wordWrap=True, textFormat=Qt.TextFormat.RichText)
        gl.addWidget(self.lbl_games)
        gl.addStretch()
        grid.addWidget(games, 1)
        lay.addLayout(grid)
        recent = q(QFrame(), objectName="card")
        rl = QVBoxLayout(recent)
        rl.setContentsMargins(14, 12, 14, 12)
        rl.setSpacing(4)
        rl.addWidget(q(QLabel("Последние события"), objectName="h2"))
        self.lbl_recent = q(QLabel("Пока ничего не происходило."), objectName="muted", wordWrap=True, textFormat=Qt.TextFormat.PlainText)  # строки журнала, данные, не HTML
        self.lbl_recent.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        rl.addWidget(self.lbl_recent)
        rl.addStretch()
        lay.addWidget(recent, 1)
        return page

    def _page_games(self) -> QWidget:
        page, lay = self._page()
        self._header(lay, "Игры", "Запущенные игры находятся сами: программа собирает их серверы и включает для них "
                                  "обход. Серверы входа и лаунчеров обход не трогает. Галочка «Обход», для серверов "
                                  "этой игры; снятая галочка оставляет игру в списке, но вне обхода.")
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Игра", "Серверов", "Пинг", "Статус", "Обход"])
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        hh.resizeSection(2, 80)
        hh.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        hh.resizeSection(3, 110)
        hh.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
        hh.resizeSection(4, 64)
        hh.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setShowGrid(False)
        self.table.verticalHeader().hide()
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self.show_games_menu)
        self.games_empty = q(QLabel(), objectName="empty", alignment=Qt.AlignmentFlag.AlignCenter, wordWrap=True)
        lay.addWidget(self.games_empty, 1)
        lay.addWidget(self.table, 1)
        r = QHBoxLayout()
        r.setSpacing(8)
        b2 = q(QPushButton("Добавить игру…"), objectName="tonal")
        b2.setToolTip("Выбрать exe игры вручную, если она не нашлась сама")
        b2.clicked.connect(self.add_game)
        b4 = q(QPushButton("Поделиться адресами…"), objectName="text")
        b4.setToolTip("Отправить найденные серверы игр в общую базу: у других игра заработает с первого запуска. "
                      "Отправляются только адреса серверов, без вашего IP. Нужен аккаунт GitHub.")
        b4.clicked.connect(self.share_games)
        self.table.itemChanged.connect(self._on_game_check)  # «Обход»: a checkbox per game
        r.addWidget(b2)
        r.addStretch()
        r.addWidget(b4)
        lay.addLayout(r)
        return page

    def _page_settings(self) -> QWidget:
        page, lay = self._page((20, 16, 20, 0))
        self._header(lay, "Настройки")
        area = q(QScrollArea(), widgetResizable=True)
        body = q(QWidget(), objectName="page")
        self.box_settings = body  # disabled while busy: a toggle would restart winws mid-pick
        bl = QVBoxLayout(body)
        bl.setContentsMargins(0, 0, 8, 24)
        bl.setSpacing(0)
        s = self.settings
        self.cb = {}
        groups = {}
        for sec, title in (("main", "Основное"), ("adv", "Дополнительно")):
            if sec == "adv":
                self.btn_adv = q(QPushButton(), objectName="text")
                self.btn_adv.setCursor(Qt.CursorShape.PointingHandCursor)
                self.btn_adv.clicked.connect(lambda: self.set_advanced(not self.adv_box.isVisible()))
                bl.addWidget(self.btn_adv, 0, Qt.AlignmentFlag.AlignLeft)
            box = q(QFrame(), objectName="group")
            gl = QVBoxLayout(box)
            gl.setContentsMargins(4, 4, 4, 4)
            gl.setSpacing(0)
            if sec == "main":
                bl.addWidget(q(QLabel(title), objectName="section"))
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
            e = q(QLineEdit(s.get(f"game_ports_{proto}") or ""), placeholderText=default)
            e.editingFinished.connect(lambda p=proto: self.on_ports(p))
            self.ports[proto] = e
            groups["adv"].addWidget(InputRow(
                f"Порты игр, {proto.upper()}",
                f"Какие порты {proto.upper()} обходить для игр (когда обход для игр включён). Через запятую, "
                f"диапазоны через дефис. Пусто, по умолчанию: {default}"
                + (" (1935 пропущен, трансляции OBS)." if proto == "tcp" else "."), e))
        bl.addWidget(q(QLabel("DNS"), objectName="section"))
        dbox = q(QFrame(), objectName="group")
        dl = QVBoxLayout(dbox)
        dl.setContentsMargins(16, 14, 16, 16)
        dl.setSpacing(8)
        self.dns_combo = QComboBox()
        self.dns_combo.setToolTip("Какой DNS ставить системе. «Авто» проверяет популярные и DNS провайдера и "
                                  "выбирает самый быстрый среди тех, кто не подменяет ответы")
        for label, mode in DNS_MODES:
            self.dns_combo.addItem(label, mode)
        idx = self.dns_combo.findData(s.get("dns_mode", "provider"))
        self.dns_combo.setCurrentIndex(max(idx, 0))
        self.dns_combo.currentIndexChanged.connect(lambda _i: self.dns_mode_changed())
        dl.addWidget(InputRow("DNS", "Резолвер для всей системы. «От провайдера» вернёт автоматические настройки.",
                              self.dns_combo, width=240))
        self.dns_edit = q(QLineEdit(s.get("dns_custom") or "", placeholderText="1.1.1.1, 1.0.0.1"))
        self.dns_edit.setToolTip("Свои серверы через запятую, только IPv4")
        self.dns_custom_row = InputRow("Свой DNS", "Адреса через запятую, например 1.1.1.1, 1.0.0.1", self.dns_edit)
        self.dns_custom_row.setVisible(self.dns_combo.currentData() == "custom")
        dl.addWidget(self.dns_custom_row)
        self.lbl_dns = q(QLabel(), objectName="muted", wordWrap=True, textFormat=Qt.TextFormat.PlainText)
        dl.addWidget(self.lbl_dns)
        sw = Switch()
        sw.setChecked(bool(s.get("pick_ignore_dns", False)))
        sw.setAccessibleName("Подбирать при подмене DNS")
        sw.setToolTip("Подбор идёт, даже если провайдер подменяет DNS. Результат может быть случайным: лучше "
                      "сначала починить DNS")
        sw.toggled.connect(lambda v: self.on_check("pick_ignore_dns", v))
        self.cb["pick_ignore_dns"] = sw
        dl.addWidget(SettingRow("Подбирать при подмене DNS",
                                "Подбор идёт, даже если провайдер подменяет DNS. Результат может быть случайным: "
                                "лучше сначала починить DNS", sw))
        drow = QHBoxLayout()
        drow.setSpacing(8)
        self.btn_dns = q(QPushButton("Проверить и применить"), objectName="primary")
        self.btn_dns.setToolTip("Проверить резолверы и поставить выбранный")
        self.btn_dns.clicked.connect(self.dns_apply)
        self.btn_dns_provider = q(QPushButton("Вернуть как у провайдера"), objectName="tonal")
        self.btn_dns_provider.setToolTip("Вернуть автоматические настройки DNS (DHCP)")
        self.btn_dns_provider.clicked.connect(self.dns_restore)
        drow.addWidget(self.btn_dns)
        drow.addWidget(self.btn_dns_provider)
        drow.addStretch()
        dl.addLayout(drow)
        bl.addWidget(dbox)
        bl.addWidget(q(QLabel("Обновления"), objectName="section"))
        upd = q(QFrame(), objectName="group")
        ul = QVBoxLayout(upd)
        ul.setContentsMargins(16, 14, 16, 16)
        ul.setSpacing(10)
        self.lbl_ver = q(QLabel(), objectName="rowTitle")
        ul.addWidget(self.lbl_ver)
        self.lbl_upd = q(QLabel(), objectName="muted", wordWrap=True, textFormat=Qt.TextFormat.PlainText)  # тег релиза, данные
        ul.addWidget(self.lbl_upd)
        self.lbl_upd.hide()  # shown once there is a result: an empty line left a gap
        r = QHBoxLayout()
        r.setSpacing(8)
        self.btn_check = q(QPushButton("Проверить обновления"), objectName="tonal")
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
        self.fill_dns()
        return page

    def fill_dns(self):
        """Что стоит сейчас: из сохранённого результата, без PowerShell на каждый тик."""
        cur = self.settings.get("dns_servers") or []
        name = self.settings.get("dns_name") or ""
        if cur:
            text = ", ".join(cur) + (f" ({name})" if name else "")
        elif self.settings.get("dns_checked"):
            text = "как у провайдера (DHCP)"  # так решила программа; что стоит на самом деле, видно в диагностике
        else:
            text = "этой программой не менялся"
        self.lbl_dns.setText("Сейчас: " + text)

    def dns_mode_changed(self):
        mode = self.dns_combo.currentData()
        self.dns_custom_row.setVisible(mode == "custom")
        self.save(dns_mode=mode)

    def _page_diag(self) -> QWidget:
        page, lay = self._page()
        self._header(lay, "Диагностика", "Поиск частых причин, по которым обход не работает: конфликтующие "
                                         "программы, зависший драйвер, подмена DNS и другое.")
        drow = QHBoxLayout()
        drow.setSpacing(8)
        self.btn_diag = q(QPushButton("Найти проблемы"), objectName="primary")
        self.btn_diag.clicked.connect(self.run_diag)
        drow.addWidget(self.btn_diag)
        self.btn_report = QPushButton("Сохранить в zip…")
        self.btn_report.setToolTip("Собрать журнал, диагностику и настройки в zip, чтобы отправить разработчику. "
                                   "Имя пользователя Windows в отчёте скрыто.")
        self.btn_report.clicked.connect(self.save_report)
        drow.addWidget(self.btn_report)
        self.btn_send = q(QPushButton("Отправить отчёт разработчику…"), objectName="tonal")
        self.btn_send.setToolTip("Не помогло? Откроется страница GitHub с готовым отчётом: версии, найденные "
                                 "проблемы и конец журнала. Имя пользователя и IP-адреса скрыты. Нужен аккаунт GitHub.")
        self.btn_send.clicked.connect(lambda: self.send_report())
        drow.addWidget(self.btn_send)
        drow.addStretch()
        lay.addLayout(drow)
        self.diag_table = QTableWidget(0, 3)
        self.diag_table.setHorizontalHeaderLabels(["Проверка", "Результат", ""])
        hh = self.diag_table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        hh.resizeSection(2, 124)  # room for «Исправить»: ResizeToContents ignored cell widgets and clipped it
        hh.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.diag_table.verticalHeader().hide()
        self.diag_table.verticalHeader().setMinimumSectionSize(40)  # fits the «Исправить» button
        self.diag_table.setShowGrid(False)
        self.diag_table.setWordWrap(True)
        self.diag_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        lay.addWidget(self.diag_table, 1)
        return page

    def _page_log(self) -> QWidget:
        page, lay = self._page()
        self._header(lay, "Журнал")
        stats = q(QFrame(), objectName="card")
        sl = QVBoxLayout(stats)
        sl.setContentsMargins(20, 14, 20, 14)
        self.lbl_stats = q(QLabel(), wordWrap=True, textFormat=Qt.TextFormat.RichText)
        sl.addWidget(self.lbl_stats)
        lay.addWidget(stats)
        self.logbox = q(QPlainTextEdit(), readOnly=True)
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
            self.list_combo.addItem(str(names.get(key, key)), key)
        self.list_combo.addItem("Программы, которые обход не трогает (exe)", "apps_exclude")  # a setting, not a file
        self.list_combo.currentIndexChanged.connect(self.load_list)
        lay.addWidget(self.list_combo)
        hint = q(QLabel(), objectName="muted", wordWrap=True)
        hint.setText("По одному домену или адресу в строке, строки с # - комментарии.")
        lay.addWidget(hint)
        self.lbl_list_count = q(QLabel(), objectName="muted")
        lay.addWidget(self.lbl_list_count)
        self.list_edit = q(QPlainTextEdit(), objectName="editor")
        self.list_edit.setMaximumHeight(260)  # не растягивать пустое поле на пол-экрана
        lay.addWidget(self.list_edit, 1)
        r = QHBoxLayout()
        r.setSpacing(8)
        b = q(QPushButton("Сохранить"), objectName="primary")
        b.setMinimumWidth(140)
        b.clicked.connect(self.save_list)
        self.btn_ipset = QPushButton("Обновить IP-список с GitHub")
        self.btn_ipset.setMinimumWidth(220)
        self.btn_ipset.setToolTip("Скачать свежий список IP-диапазонов (Cloudflare и др.) для обхода")
        self.btn_ipset.clicked.connect(self.update_ipset)
        b_open = q(QPushButton("Открыть папку списков"), objectName="text")
        b_open.setToolTip("Открыть папку с файлами списков в проводнике")
        b_open.clicked.connect(lambda: os.startfile(str(paths.LISTS)))
        r.addWidget(b)
        r.addWidget(self.btn_ipset)
        r.addStretch()
        r.addWidget(b_open)
        lay.addLayout(r)
        return page

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
            app = QApplication.instance()
            if isinstance(app, QApplication):
                app.setStyleSheet(style.build_style())
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
            w = it.widget() if it is not None else None
            if w is not None:
                w.hide()  # deleteLater waits for the event loop: the old chip showed under the new
                w.deleteLater()
        res = dict(res)
        speed = res.pop("Скорость YouTube", None)  # one YouTube chip: "медленно" when it opens but crawls
        for name, ok in res.items():
            slow = name == "YouTube" and ok and speed is False
            self.chips.addWidget(StatusChip(name, ok and not slow, "медленно" if slow else ""))
        self.chips.addStretch()

    def set_advanced(self, on: bool, save: bool = True):
        """Show/hide expert settings and the raw lists editor."""
        self.adv_box.setVisible(on)
        self.tabs.setTabVisible(self.tab_lists, on)
        self.btn_adv.setText("Дополнительно ▾" if on else "Дополнительно ▸")
        if save:
            self.save(show_advanced=on)

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
            for t, n in s["switches"][-3:]:  # одна строка на смену: длинная строка рвалась на три
                rows.append(("Смена стратегии", f"{time.strftime('%d.%m %H:%M', time.localtime(t))} · {n}"))
        muted = style.C["on_surface_variant"]
        self.lbl_stats.setText(
            f"<div style='font-weight:600; margin-bottom:6px'>За последние {s['days']} дней</div>"
            "<table cellspacing='0' cellpadding='3'>"
            + "".join(f"<tr><td style='color:{muted}; padding-right:18px'>{html.escape(str(k))}</td>"
                      f"<td style='text-align:right'>{html.escape(str(v))}</td></tr>" for k, v in rows)
            + "</table>")

    def show_strategy_help(self):
        """«?» у стратегии: та же подсказка, что при наведении, но по нажатию и без окна."""
        pos = self.btn_desc_help.mapToGlobal(QPoint(0, self.btn_desc_help.height() + 2))
        QToolTip.hideText()
        QToolTip.showText(pos, self.describe_full(), self.btn_desc_help)

    def describe(self) -> str:
        """Коротко: «комбинация ALT8+FTAA+ALT11». Полные имена и группы - в describe_full()."""
        if self._zapret2_on():
            from app import zapret2
            return zapret2.NAME
        c = self.settings.get("strategy")
        if not c:
            return "не выбрана"
        if c.get("picks"):
            names = dict.fromkeys([c["base"], *c["picks"].values()])
            return "комбинация " + "+".join(short_name(n) for n in names)
        return c["base"]

    def describe_full(self) -> str:
        """Полные имена и группы для подсказки: «комбинация ALT8 + FAKE TLS AUTO ALT (Google)»."""
        if self._zapret2_on():  # у zapret2 своих стратегий нет: та же строка, что и в статусе
            return self.describe()
        c = self.settings.get("strategy") or {}
        if not c.get("picks"):
            return self.describe()
        by_name: dict[str, list[str]] = {}
        for g, n in c["picks"].items():
            by_name.setdefault(n, []).append(group_name(g))
        parts = [c["base"]] + [f"{n} ({', '.join(gs)})" for n, gs in by_name.items() if n != c["base"]]
        return "комбинация " + " + ".join(parts)

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
        if self.status.text() != title:
            self.status.setText(title)
        desc = self.describe()  # once per tick: it builds strings and reads the saved strategy
        full = self.describe_full()  # tooltip: abbreviations in the status line, full names on hover
        if getattr(self, "_desc_tip", None) != full:
            self._desc_tip = full
            self.substatus.setToolTip(full)
            self.btn_desc_help.setToolTip(full)
        # «?» нужен, только когда подсказка добавляет что-то к строке статуса
        want_help = not self.busy and not self.error and full != desc
        if self.btn_desc_help.isHidden() == want_help:
            self.btn_desc_help.setVisible(want_help)
        sub = ""
        if self.error and not self.busy:
            sub = self.error
        elif not self.busy:
            # две строки в выключенном состоянии: «?» встаёт в конце строки со стратегией, а не у края окна
            sub = f"Стратегия: {desc}" if run else "Нажмите кнопку, чтобы включить.\nСтратегия: " + desc
        if sub and self.substatus.text() != sub:
            self.substatus.setText(sub)
        err = "yes" if self.error and not self.busy else "no"
        if self.substatus.property("error") != err:
            self.substatus.setProperty("error", err)
            self.substatus.style().unpolish(self.substatus)
            self.substatus.style().polish(self.substatus)
        last = getattr(self, "_last_check", None)
        tip = f"ZapretAuto, {title}\n{desc}"
        if last:
            ok = "всё работает" if last[1] else "есть сбои"
            tip += f"\nпроверка {time.strftime('%H:%M', time.localtime(last[0]))}: {ok}"
        if getattr(self, "_tray_tip", None) != tip:
            self._tray_tip = tip
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
        idx = max(0, self.combo.findText(c["base"])) if manual and c else 0
        if self.combo.currentIndex() != idx:
            self.combo.setCurrentIndex(idx)

    def fill_games(self):
        games = self.watcher.games() if self.watcher else {}
        ign = {x.lower() for x in self.settings.get("games_ignore", [])}
        excl = {x.lower() for x in self.settings.get("games_exclude", [])}
        capture = bool(self.settings.get("game_capture", True))
        groups: dict[str, dict] = {}
        for exe, g in games.items():
            if exe in ign:
                continue
            name = gamewatch.display_name(exe, g.get("path"))
            row = groups.setdefault(name, {"name": name, "exes": [], "ips": 0, "ping": None, "active": False,
                                           "manual": False})
            row["exes"].append(exe)
            row["ips"] += len(g.get("ips") or ())
            row["manual"] = row["manual"] or bool(g.get("manual"))
            ping = g.get("ping_ms")
            if ping is not None and (row["ping"] is None or ping < row["ping"]):
                row["ping"] = ping
            row["active"] = row["active"] or bool(g.get("active"))
        rows = [r for r in groups.values() if r["ips"] or r["active"] or r["manual"]]
        for r in rows:  # вне обхода, только если исключены все процессы этой игры
            r["out"] = not capture or all(e in excl for e in r["exes"])
        self.games_empty.setVisible(not rows)
        self.games_empty.setText(
            "Пока игр не найдено. Запустите игру, она появится здесь сама." if self.settings.get("game_watch") else
            "Включите «Следить за играми» в настройках, запущенные игры появятся здесь сами.")
        self.table.setVisible(bool(rows))
        self.table.blockSignals(True)  # a check state set while filling must not call back
        try:
            self._sync_games_table(rows, capture)
        finally:
            self.table.blockSignals(False)
        self._fill_home_games(rows)

    def _sync_games_table(self, rows: list[dict], capture: bool) -> None:
        """Update rows in place: a full rebuild every 2 s reset the selection and flickered."""
        keys = [r["name"] for r in rows]
        rebuild = getattr(self, "_games_keys", None) != keys
        if rebuild:
            cur = self.table.currentRow()
            item = self.table.item(cur, 0) if cur >= 0 else None
            cur_name = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
            self._games_keys = keys
            self.table.setRowCount(len(rows))
            for i, r in enumerate(rows):
                name = QTableWidgetItem()
                name.setData(Qt.ItemDataRole.UserRole, r["exes"][0])
                name.setData(Qt.ItemDataRole.UserRole + 1, tuple(r["exes"]))
                name.setToolTip("\n".join(r["exes"]))
                self.table.setItem(i, 0, name)
                for j in (1, 2, 3):
                    self.table.setItem(i, j, QTableWidgetItem())
                self.table.setItem(i, 4, QTableWidgetItem())
            if cur_name in keys:
                self.table.setCurrentCell(keys.index(cur_name), 0)
        for i, r in enumerate(rows):
            status = "вне обхода" if r["out"] else ("играете" if r["active"] else "—")
            values = (r["name"], str(r["ips"]), "—" if r["ping"] is None else f"{r['ping']:.0f}", status)
            for j, text in enumerate(values):
                it = self.table.item(i, j)
                if it is None:
                    it = QTableWidgetItem()
                    self.table.setItem(i, j, it)
                if it.text() != text:
                    it.setText(text)
            self.table.item(i, 1).setData(Qt.ItemDataRole.EditRole, r["ips"])  # сортировка по числу
            box = self.table.item(i, 4)
            want = Qt.CheckState.Unchecked if r["out"] else Qt.CheckState.Checked
            if box.checkState() != want:
                box.setCheckState(want)
        if rebuild or getattr(self, "_games_capture", None) != capture:
            self._games_capture = capture
            tip = ("Перехват игр выключен в настройках" if not capture else
                   "Снимите галочку, серверы этой игры останутся вне обхода, игра останется в списке")
            flags = (Qt.ItemFlag.NoItemFlags if not capture else
                     (Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable))
            for i in range(len(rows)):
                box = self.table.item(i, 4)
                if box is not None:
                    box.setToolTip(tip)
                    box.setFlags(flags)

    def _fill_home_games(self, rows: list[dict]) -> None:
        """Home card: running games first, then the ones with most servers; three at most."""
        if not self.settings.get("game_watch"):
            text = "Слежение за играми выключено, включите его в «Настройках»."
        elif not rows:
            text = "Пока игр не найдено. Запустите игру, она появится здесь сама."
        else:
            top = sorted(rows, key=lambda r: (not r["active"], -r["ips"]))[:3]
            lines = []
            for r in top:
                if r["out"]:
                    state = "вне обхода"
                elif r["active"]:
                    state = (f"<span style='color:{style.C["good"]}'>играете</span>"
                             + (f", {r['ping']:.0f} мс" if r["ping"] is not None else ""))
                else:
                    state = f"{r['ips']} адресов в обходе"
                lines.append(f"<span style='color:{style.C["on_surface"]}'>{html.escape(r['name'])}</span>, {state}")
            more = len(rows) - len(top)
            text = "<br>".join(lines) + (f"<br>и ещё {more}" if more > 0 else "")
        if self.lbl_games.text() != text:
            self.lbl_games.setText(text)

    def fill_isp(self):
        """«Провайдер: X · в общей базе N отчётов», and whether most of them use the same combination."""
        if tester.vpn_active():
            self.lbl_isp.hide()
            return
        from app import sharedb
        def work(log):
            who = sharedb.isp()
            return (who, sharedb.isp_entry(who[0], updater.current_version())) if who else None
        def done(r):
            if not r:
                self.lbl_isp.hide()
                return
            (asn, name), e = r
            text = f"Провайдер: {name or asn}"
            if e is None:  # the base is unreachable: say nothing about it
                pass
            elif e.get("reports"):
                same = e.get("choice") == self.settings.get("strategy")
                text += (f" · в общей базе отчётов: {e['reports']}"
                         + (", у вас та же комбинация, что у большинства" if same else ""))
            else:
                text += " · в общей базе пока нет отчётов"
            self.lbl_isp.setText(text)
            self.lbl_isp.show()
        self._run(work, done)

    def _pick_summary(self):
        """What the pick achieved, in one line (log + tray), and the service chips from its final runs."""
        lp = self.settings.get("last_pick") or {}
        if not lp.get("total") or time.time() - lp.get("time", 0) > 600:
            return
        line = f"Готово: {self.describe()}, проходит {lp['score']} из {lp['total']} проверок"
        if lp.get("before") is not None:
            line += f" (было {lp['before']})"
        if lp.get("services"):
            self.set_chips(lp["services"])
            bad = [k for k, v in lp["services"].items() if not v and k != "Скорость YouTube"]
            if bad:
                line += ". Не открывается: " + ", ".join(bad)
        self.log(line)
        self.notify(line)
