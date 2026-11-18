"""Окно программы: трей и жизненный цикл; страницы, действия, игры и обновления, в отдельных миксинах."""
import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon, QWidget

from app import logs, stats
from app.gui.shared import AUTO, COLORS, UPDATE_EVERY, Bridge, Pages, app_icon, gamewatch, q, style, tester, updater  # noqa: F401
from app.gui.sitecheck import _site_check, _with_direct_path  # noqa: F401
from app.gui.window_actions import ActionsMixin
from app.gui.window_games import GamesMixin
from app.gui.window_pages import PagesMixin
from app.gui.window_updates import UpdatesMixin
from app.runner import Runner


class MainWindow(PagesMixin, ActionsMixin, GamesMixin, UpdatesMixin, QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("ZapretAuto")
        self.resize(900, 620)
        self.setMinimumSize(880, 520)
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
        self._wd_repaired_at = 0.0  # последняя автопочинка службы WinDivert
        self._repick_pending = False
        self._net = None
        self._new_net = False  # сеть сменилась: можно подбирать без паузы
        self._net_remembered = False  # текущая сеть уже записана в общую базу
        self._last_check = 0.0
        self._vpn_was = None
        self._next_health = time.time() + 30  # status chips soon after start
    # ---------- UI ----------
    SETTINGS = [  # key, title, supporting text, default, section
        ("game_watch", "Следить за играми",
         "Находить запущенные игры и включать обход для их серверов. Если с обходом стало хуже, адрес убирается.",
         False, "main"),
        ("game_capture", "Перехватывать подключения игр",
         "Добавлять серверы найденных игр в обход zapret. Выключите, если с обходом игра работает хуже: серверы "
         "игр останутся вне обхода, а слежение за играми можно оставить.", True, "main"),
        ("auto_repick", "Чинить обход самостоятельно",
         "Каждые 10 минут и при смене сети проверять Discord и YouTube. Если не открываются дважды подряд, "
         "подобрать стратегию заново (не посреди игры).", True, "main"),
        ("auto_update", "Обновляться автоматически",
         "Ставить новые версии zapret и ZapretAuto при запуске и раз в 6 часов, но не во время игры.", True, "main"),
        ("quiet_updates", "Обновляться только в тишине",
         "Не ставить обновления, пока идёт звонок (Discord, Telegram, Zoom…) или открыто полноэкранное приложение "
         ", игра, фильм, презентация. Обновление перезапускает обход, и связь рвётся на минуту. "
         "Дольше суток не откладывается.", False, "main"),
        ("discord_after_vpn", "Перезапускать Discord после VPN",
         "Когда VPN выключается, Discord остаётся на старых соединениях и висит на «Подключение». "
         "Программа сама перезапустит его, чтобы он заработал через обход.", True, "main"),
        ("autostart", "Запускать вместе с Windows",
         "ZapretAuto тихо стартует в трее при входе в Windows.", False, "main"),
        ("notifications", "Уведомления в трее",
         "Всплывающие сообщения в трее: проверки, починка обхода, ошибки. Если выключить, всё останется только "
         "в журнале.", True, "main"),
        ("auto_hostlist", "Находить заблокированные сайты",
         "Обход сам замечает сайты, которые не открываются из-за DPI, и включает для них обход. "
         "Найденное, в разделе «Списки» → list-auto.txt.", True, "adv"),
        ("game_udp", "Обход UDP для игр",
         "Обход UDP-трафика игр на портах 1024-65535. Включается сам, когда найдена игра.", False, "adv"),
        ("game_tcp", "Обход TCP для игр",
         "То же для TCP: некоторые игры входят в онлайн по TCP. Включается сам, когда найдена игра. "
         "Порт 1935 (трансляции OBS) не трогает.", False, "adv"),
        ("games_web_bypass", "Обход сайтов игр",
         "Трогать адреса сайтов и логинов игр (Cloudflare, CloudFront). Обычно нужен: без него вход в игру может "
         "зависнуть. Выключите, если из-за него не грузятся картинки в игре.", True, "adv"),
        ("telegram", "Telegram (эксперимент)",
         "Добавить подсети Telegram в обход. Помогает не всем провайдерам: при блокировке по IP нужен прокси или VPN.",
         False, "adv"),
        ("zapret2", "zapret2 (эксперимент)",
         "Новый движок: если сайт начинает блокироваться, обход сам переключает для него другую стратегию, "
         "без перезапуска и подбора. При первом включении скачивается ~4 МБ.", False, "adv"),
        ("zapret2_auto", "Переключать движок самому",
         "Если обычный обход перестал работать и подбор не нашёл ни одной рабочей стратегии, программа предложит "
         "включить zapret2, а потом делает это сама. Не чаще раза в 6 часов.", True, "adv"),
    ]
    # ---------- diagnostics / lists ----------
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
        a8 = QAction("Сохранить отчёт в zip…", m)
        a8.triggered.connect(lambda: (self.show_window(), self.save_report()))
        self.menu_strat = QMenu("Стратегия", m)
        self.menu_strat.aboutToShow.connect(self._fill_tray_strategies)
        self.menu_games = QMenu("Игры", m)
        self.menu_games.aboutToShow.connect(self._fill_tray_games)
        for a in (self.act_toggle, a6, a5):
            m.addAction(a)
        m.addSeparator()
        m.addMenu(self.menu_strat)
        m.addMenu(self.menu_games)
        m.addAction(a2)
        m.addSeparator()
        for a in (a3, a7, a8, a4):
            m.addAction(a)
        self._menu = m
        self.tray.setContextMenu(m)
        self.tray.activated.connect(lambda r: self.show_window() if r in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick) else None)
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
    def show_window(self):
        self.show()
        self.setWindowState(self.windowState() & ~Qt.WindowState.WindowMinimized)
        self.raise_()
        self.activateWindow()
    def closeEvent(self, e):
        e.ignore()
        self.hide()
        if not self._hint_shown:
            self._hint_shown = True
            self.notify("Программа свёрнута в трей и продолжает работать. "
                                  "Выход, правый клик по значку → «Выход».")
    def save(self, **kw):
        tester.save_settings(**kw)
        self.settings = tester.load_settings()
    # ---------- runner ----------
    def _fill_tray_strategies(self):
        m = self.menu_strat
        m.clear()
        for i in range(self.combo.count()):
            a = m.addAction(self.combo.itemText(i))
            a.setCheckable(True)
            a.setChecked(i == self.combo.currentIndex())
            a.setEnabled(not self.busy)  # a pick or an update is running: switching would restart winws mid-way
            a.triggered.connect(lambda _=False, i=i: self._tray_strategy(i))
    def _tray_strategy(self, i: int):
        if self.busy or i == self.combo.currentIndex():
            return
        self.combo.setCurrentIndex(i)
        self.on_combo(i)
        self.log(f"стратегия из трея: {self.combo.itemText(i)}")
    def _fill_tray_games(self):
        """Found games with a check mark: unchecking leaves that game's servers out of the bypass, checking returns."""
        m = self.menu_games
        m.clear()
        games = self.watcher.games() if self.watcher else {}
        excl = {x.lower() for x in self.settings.get("games_exclude", [])}
        if not self.settings.get("game_watch"):
            m.addAction("Слежение за играми выключено").setEnabled(False)
            return
        if not self.settings.get("game_capture", True):
            m.addAction("Перехват игр выключен в настройках").setEnabled(False)
            return
        games = {**{exe: {} for exe in excl}, **games}  # excluded ones stay listed: a click brings them back
        if not games:
            m.addAction("Игр пока не найдено").setEnabled(False)
            return
        for exe, g in sorted(games.items(), key=lambda kv: (not kv[1].get("active"), kv[0] in excl, kv[0])):
            a = m.addAction(gamewatch.display_name(exe, g.get("path")) + (", играете" if g.get("active") else "")
                            + (", вне обхода" if exe in excl else ""))
            a.setCheckable(True)
            a.setChecked(exe not in excl)
            a.triggered.connect(lambda on, exe=exe: self.set_game_excluded(exe, not on))
    # ---------- autopick ----------
    # ---------- games ----------
    # ---------- updates ----------
    # ---------- lifecycle ----------
    def startup(self, autostart=True):
        if self.settings.get("autostart"):
            self.set_autostart(True)  # refresh the task (adds --tray for older installs)
        if not autostart:
            self.log("ZAPRET_NO_AUTOSTART: автозапуск отключён")
            return
        self._next_update_check = time.time() + UPDATE_EVERY
        if self.settings.get("auto_update", True) and updater:
            if not self._update_deferred(time.time()):  # deferred: the timer retries in 10 min
                self._auto_check = True
                self.check_update()
        if tester.chosen_strategy():
            self.start_runner()
        else:
            self.ask_autopick()
        if updater:
            QTimer.singleShot(5000, self.fill_isp)
        if self.settings.get("game_watch"):
            self.start_watcher()
    def quit(self):
        self._cancel = True  # let a running pick stop at its next step
        if self.runner.running:
            stats.record("stop")  # else the next start's uptime counted the time the PC was off
        self.runner.stop()
        self.stop_watcher()
        self.tray.hide()
        for w in list(self._workers):  # let running jobs (downloads, picks) end before the process exits
            w.wait(3000)
        QApplication.quit()
