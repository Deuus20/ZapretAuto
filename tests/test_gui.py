"""Тесты окна и трея: поднимают QApplication и MainWindow."""
import time

import pytest
from helpers import _close, _gui

from app import gamewatch, lists, paths, settings, sharedb, tester, updater, zapret2


def test_gui_smoke(tmp_path, monkeypatch):
    """Build the main window offscreen and walk its tabs (skipped where PySide6 is absent, e.g. CI)."""
    pytest.importorskip("PySide6")
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setattr(paths, "DATA", tmp_path)
    monkeypatch.setattr(paths, "LISTS", tmp_path / "lists")
    settings._cache = None
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from app.gui.window import MainWindow
    w = MainWindow()
    try:
        w.set_advanced(True, save=False)
        for i in range(w.tabs.count()):
            w.tabs.setCurrentIndex(i)
        w.fill_games()
        w.fill_stats()
        w.set_chips({"Discord": True, "YouTube": False})
        w.refresh()
        app.processEvents()
        assert "Обход" in w.status.text()
    finally:
        w.tray.hide()
        w._timer.stop()
        w.deleteLater()  # destroy Qt objects before the interpreter exits (else a crash at shutdown)
        app.processEvents()

def test_gui_timed_pause(tmp_path, monkeypatch):
    pytest.importorskip("PySide6")
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setattr(paths, "DATA", tmp_path)
    monkeypatch.setattr(paths, "LISTS", tmp_path / "lists")
    settings._cache = None
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from app.gui.window import MainWindow
    w = MainWindow()
    try:
        state = {"on": True}
        monkeypatch.setattr(type(w.runner), "running", property(lambda self: state["on"]))
        monkeypatch.setattr(w.runner, "stop", lambda: state.update(on=False))
        monkeypatch.setattr(w, "start_runner", lambda: state.update(on=True))
        w.tray.showMessage = lambda *a, **k: None
        w.settings["ipset_updated"] = time.time()  # else _tick() starts the weekly ipset download in a thread
        w.pause(1)
        assert state["on"] is False
        w._resume_at = time.time() - 1  # pretend the minute is over
        w._tick()
        assert state["on"] is True and w._resume_at == 0
    finally:
        w.tray.hide()
        w._timer.stop()
        w.deleteLater()  # destroy Qt objects before the interpreter exits (else a crash at shutdown)
        app.processEvents()

def test_gui_game_ips_do_not_restart_winws(tmp_path, monkeypatch):
    pytest.importorskip("PySide6")
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setattr(paths, "DATA", tmp_path)
    monkeypatch.setattr(paths, "LISTS", tmp_path / "lists")
    settings._cache = None
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from app.gui.window import MainWindow
    w = MainWindow()
    try:
        w.settings["ipset_updated"] = time.time()
        w.tray.showMessage = lambda *a, **k: None
        restarts = []
        monkeypatch.setattr(type(w.runner), "running", property(lambda self: True))
        monkeypatch.setattr(w, "start_runner", lambda: restarts.append(1))
        games = {"g.exe": {"ips": {"1.2.3.4"}, "active": True, "started": time.time() - 999}}
        w.watcher = type("W", (), {"games": lambda self: games})()
        w._run_flags = (True, True)            # game filter already on: new IPs need no restart
        w.on_games_changed()
        assert restarts == []
        w._run_flags = (False, False)          # first game: filter must be switched on
        w.on_games_changed()
        assert restarts == [1]
    finally:
        w.watcher = None
        w.tray.hide()
        w._timer.stop()
        w.deleteLater()
        app.processEvents()

def test_gui_workers_do_not_leak_handles(tmp_path, monkeypatch):
    """Each finished job must release its thread handle (a reference cycle once leaked ~2 handles per job)."""
    pytest.importorskip("PySide6")
    import os

    import psutil
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setattr(paths, "DATA", tmp_path)
    monkeypatch.setattr(paths, "LISTS", tmp_path / "lists")
    settings._cache = None
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from app.gui.window import MainWindow
    w = MainWindow()
    me = psutil.Process()
    def run_batch(n):
        for _ in range(n):
            w._run(lambda log: None, lambda r: None)
            end = time.time() + 5
            while w._workers and time.time() < end:
                app.processEvents()
                time.sleep(0.005)
        for _ in range(20):
            app.processEvents()
    try:
        run_batch(10)  # warm-up
        before = me.num_handles()
        run_batch(30)
        assert me.num_handles() - before <= 3
    finally:
        w.tray.hide()
        w._timer.stop()
        w.deleteLater()
        app.processEvents()

def test_gui_auto_item_returns_to_last_pick(tmp_path, monkeypatch):
    pytest.importorskip("PySide6")
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setattr(paths, "DATA", tmp_path)
    monkeypatch.setattr(paths, "LISTS", tmp_path / "lists")
    settings._cache = None
    settings.save_settings(strategy={"base": "ALT8", "picks": {}}, auto_choice={"base": "ALT8", "picks": {}},
                           ipset_updated=time.time())
    from PySide6.QtWidgets import QApplication, QMessageBox
    app = QApplication.instance() or QApplication([])
    from app.gui.window import MainWindow
    shown = []
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: shown.append(a)))
    w = MainWindow()
    try:
        assert w.combo.currentIndex() == 0                     # single-strategy pick still shows as «Авто»
        w.on_combo(w.combo.findText("EXP"))
        assert w.settings["strategy"]["base"] == "EXP" and w.combo.currentText() == "EXP"
        w.on_combo(0)                                          # back to the pick result, no "not picked" message
        assert w.settings["strategy"] == {"base": "ALT8", "picks": {}} and not shown
    finally:
        w.tray.hide()
        w._timer.stop()
        w.deleteLater()
        app.processEvents()

def test_gui_add_game_shows_it_and_turns_watch_on(tmp_path, monkeypatch):
    pytest.importorskip("PySide6")
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setattr(paths, "DATA", tmp_path)
    monkeypatch.setattr(paths, "LISTS", tmp_path / "lists")
    settings._cache = None
    settings.save_settings(strategy={"base": "ALT8", "picks": {}}, ipset_updated=time.time(),
                           game_watch=False, games_ignore=["mygame.exe"])
    from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox
    app = QApplication.instance() or QApplication([])
    from app.gui.window import MainWindow
    monkeypatch.setattr(gamewatch.GameWatcher, "start", lambda self: None)  # no WinDivert in tests
    monkeypatch.setattr(QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: ("D:/Games/X/MyGame.exe", "")))
    w = MainWindow()
    try:
        w.add_game()
        st = settings.load_settings()
        assert st["game_watch"] and w.cb["game_watch"].isChecked() and w.watcher
        assert "mygame.exe" not in st["games_ignore"]               # «Игнорировать» undone
        assert "mygame.exe" in st["games"]                          # persisted right away
        from PySide6.QtCore import Qt
        assert w.table.rowCount() == 1 and w.table.item(0, 0).data(Qt.UserRole) == "mygame.exe" and w.table.item(0, 0).text() == "MyGame"  # shown before any traffic
        assert gamewatch.classify(r"D:\Games\X\MyGame.exe", st)     # its traffic will count as a game
        shown = []
        monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: shown.append(a[2])))
        w.add_game()                                                 # second time: says so, adds nothing
        assert shown and "уже есть" in shown[0] and w.table.rowCount() == 1
        w.watcher._games["mygame.exe"]["ips"].add("9.9.9.9")
        w.table.selectRow(0)
        w.ignore_game()                                              # not a game: its servers leave the ipset
        assert not w.watcher.games()["mygame.exe"]["ips"] and w.table.rowCount() == 0
    finally:
        if w.watcher:
            w.watcher.stop()
        w.tray.hide()
        w._timer.stop()
        w.deleteLater()
        app.processEvents()

def test_gui_update_check_shows_result(tmp_path, monkeypatch):
    pytest.importorskip("PySide6")
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setattr(paths, "DATA", tmp_path)
    monkeypatch.setattr(paths, "LISTS", tmp_path / "lists")
    settings._cache = None
    settings.save_settings(strategy={"base": "ALT8", "picks": {}}, ipset_updated=time.time())
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from app.gui.window import MainWindow
    monkeypatch.setattr(updater, "check_async", lambda cb: cb("1.10.3", False))
    monkeypatch.setattr(updater, "app_check_async", lambda cb: cb(None, False))
    w = MainWindow()
    try:
        w.btn_check.click()
        app.processEvents()
        t = w.lbl_upd.text()
        assert "1.10.3, последняя версия" in t and "не удалось проверить" in t and w.btn_check.isEnabled()
    finally:
        w.tray.hide()
        w._timer.stop()
        w.deleteLater()
        app.processEvents()

def test_gui_zapret_update_keeps_working_strategy(tmp_path, monkeypatch):
    pytest.importorskip("PySide6")
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setattr(paths, "DATA", tmp_path)
    monkeypatch.setattr(paths, "LISTS", tmp_path / "lists")
    settings._cache = None
    settings.save_settings(strategy={"base": "ALT8", "picks": {}}, ipset_updated=time.time())
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from app.gui.window import MainWindow
    w = MainWindow()
    try:
        picks, res = [], {"Discord": True, "YouTube": True}
        monkeypatch.setattr(w, "start_runner", lambda: True)
        monkeypatch.setattr(w, "autopick", lambda *a: picks.append(1))
        monkeypatch.setattr(w, "_run", lambda fn, done, line=None: done(fn(None)))
        monkeypatch.setattr(tester, "quick_check", lambda light=False: dict(res))
        w._update_done(True, True, True)
        assert not picks and not w.busy                 # still works: no 2-4 min re-pick
        res["YouTube"] = False
        w._update_done(True, True, True)
        assert picks == [1]                             # broken by the update: re-pick
    finally:
        w.tray.hide()
        w._timer.stop()
        w.deleteLater()
        app.processEvents()

def test_gui_zapret2_toggle(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    app, w = _gui(tmp_path, monkeypatch)
    try:
        started, shown = [], []
        monkeypatch.setattr(w, "start_runner", lambda: started.append(tester.chosen_strategy().name) or True)
        monkeypatch.setattr(w, "_run", lambda fn, done, line=None: done(fn(lambda m: None)))
        monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: shown.append(a[2])))
        monkeypatch.setattr(zapret2, "install", lambda log: False)          # download failed
        w._expect_running = True
        w.cb["zapret2"].setChecked(True)
        assert not w.cb["zapret2"].isChecked() and not settings.load_settings()["zapret2"] and "zapret2" in w.error
        assert started == ["ALT8"]                                           # stays on the normal engine
        monkeypatch.setattr(zapret2, "install", lambda log: True)
        monkeypatch.setattr(zapret2, "installed", lambda: True)
        w.error = ""
        w.cb["zapret2"].setChecked(True)
        assert started[-1] == zapret2.NAME and w.describe() == zapret2.NAME
        w.refresh()
        assert not w.combo.isEnabled()                                       # list of winws strategies is moot
        w.show()
        w.autopick()
        assert shown and "zapret2" in shown[-1] and not w.busy               # no pointless 2-4 min pick
        w._health_fails = 1
        monkeypatch.setattr(tester, "quick_check", lambda light=False: {"Discord": False, "YouTube": True})
        w.runner.proc = type("P", (), {"poll": lambda self: None})()          # looks running
        w._health()
        assert not w.busy and w._health_fails == 0
        w.runner.proc = None
        w.cb["zapret2"].setChecked(False)
        assert started[-1] == "ALT8" and w.combo.isEnabled()
    finally:
        _close(app, w)

def test_gui_share_games(tmp_path, monkeypatch):
    import webbrowser

    from PySide6.QtWidgets import QMessageBox
    app, w = _gui(tmp_path, monkeypatch)
    try:
        opened, shown = [], []
        monkeypatch.setattr(webbrowser, "open", lambda u: opened.append(u))
        monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: shown.append(a[2])))
        monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes))
        w.share_games()
        assert shown and not opened                                          # nothing collected yet
        monkeypatch.setattr(gamewatch.GameWatcher, "start", lambda self: None)
        w.start_watcher()
        w.watcher._games["game.exe"] = {"path": "x", "ips": {"9.9.9.9"}, "active": False, "ping_ms": None}
        w.share_games()
        assert opened and "%5Bgames%5D" in opened[0] and "9.9.9.9" in opened[0]
    finally:
        w.stop_watcher()
        _close(app, w)

def test_gui_known_games_show_after_restart(tmp_path, monkeypatch):
    """Bug: after an app update/restart the games table stayed empty until a game got a new IP."""
    monkeypatch.setattr(gamewatch.GameWatcher, "start", lambda self: None)
    app, w = _gui(tmp_path, monkeypatch, game_watch=True, games_schema=2,
                  games={"deadlock.exe": {"path": "x", "ips": ["9.9.9.9"]}})
    try:
        w.start_watcher()
        from PySide6.QtCore import Qt
        assert w.table.rowCount() == 1 and w.table.item(0, 0).data(Qt.UserRole) == "deadlock.exe" and "Deadlock" in w.lbl_games.text() and not w.table.isHidden()
        assert not w.btn_reset.isEnabled()                   # nothing selected yet
        w.table.selectRow(0)
        assert w.btn_reset.isEnabled() and w.btn_ignore.isEnabled()
    finally:
        w.stop_watcher()
        _close(app, w)

def test_gui_chips_and_switch_rows(tmp_path, monkeypatch):
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import QMouseEvent

    from app.gui.widgets import SettingRow, StatusChip
    app, w = _gui(tmp_path, monkeypatch)
    try:
        w.set_chips({"Discord": True, "YouTube": True, "Скорость YouTube": False, "Голос Discord": False})
        chips = [w.chips.itemAt(i).widget() for i in range(w.chips.count()) if w.chips.itemAt(i).widget()]
        assert [c.toolTip() for c in chips] == ["Discord: работает", "YouTube: медленно", "Голос Discord: не открывается"]
        assert all(isinstance(c, StatusChip) for c in chips)
        row = w.cb["autostart"].parent()
        assert isinstance(row, SettingRow) and row.toolTip()           # description on hover
        monkeypatch.setattr(w, "set_autostart", lambda on: None)        # no schtasks in tests
        before = w.cb["autostart"].isChecked()
        pos = QPointF(5, row.height() / 2)
        row.mouseReleaseEvent(QMouseEvent(QMouseEvent.MouseButtonRelease, pos, pos, Qt.LeftButton, Qt.LeftButton,
                                          Qt.NoModifier))
        assert w.cb["autostart"].isChecked() != before and settings.load_settings()["autostart"] != before
        w.set_advanced(False, save=False)
        lists_i = w.tab_lists
        assert w.tabs.rail.buttons[lists_i].isHidden()                   # Lists only with «Дополнительно»
    finally:
        _close(app, w)

def test_gui_pick_summary(tmp_path, monkeypatch):
    app, w = _gui(tmp_path, monkeypatch, last_pick={"score": 26, "total": 28, "before": 18, "time": time.time(),
                                                    "services": {"Discord": True, "YouTube": True,
                                                                 "Голос Discord": False}})
    try:
        shown = []
        monkeypatch.setattr(w.tray, "showMessage", lambda *a: shown.append(a[1]))
        w._pick_summary()
        assert shown and "26 из 28" in shown[0] and "было 18" in shown[0] and "Голос Discord" in shown[0]
        assert w.chips.count() >= 3
    finally:
        _close(app, w)

def test_gui_follows_windows_theme(tmp_path, monkeypatch):
    from app.gui import style
    app, w = _gui(tmp_path, monkeypatch)
    try:
        monkeypatch.setattr(style, "windows_light", lambda: True)
        w.apply_theme()
        assert style.C["surface"] == style.LIGHT["surface"] and style.LIGHT["surface"] in app.styleSheet()
        monkeypatch.setattr(style, "windows_light", lambda: False)
        w.apply_theme()
        assert style.C == style.DARK and style.DARK["surface"] in app.styleSheet()
    finally:
        style.set_theme(False)  # tokens are global: leave the dark ones for other tests
        _close(app, w)

def test_gui_site_broken_by_bypass_gets_excluded(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QInputDialog, QMessageBox
    app, w = _gui(tmp_path, monkeypatch)
    try:
        paths.sync_lists()
        excl = paths.LISTS / "list-exclude-user.txt"
        monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("https://bank.example.ru/x", True)))
        shown = []
        monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: shown.append(a[2])))
        monkeypatch.setattr(w, "_run", lambda fn, done, line=None: done(fn(lambda m: None)))
        monkeypatch.setattr("time.sleep", lambda s: None)
        monkeypatch.setattr("socket.gethostbyname", lambda d: "1.2.3.4")
        w.runner.proc = type("P", (), {"poll": lambda self: None})()
        # opens only while excluded from the bypass
        monkeypatch.setattr(tester, "probe", lambda t: 0.1 if "bank.example.ru" in excl.read_text(encoding="utf-8")
                            and t.url.startswith("https") else None)
        w.check_site()
        assert shown and "ломался из-за обхода" in shown[-1] and "bank.example.ru" in excl.read_text(encoding="utf-8")
        assert "bank.example.ru" not in (paths.LISTS / "list-general-user.txt").read_text(encoding="utf-8")
        w.runner.proc = None
    finally:
        _close(app, w)

def test_gui_exclude_app(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog, QMessageBox
    monkeypatch.setattr(gamewatch.GameWatcher, "start", lambda self: None)
    app, w = _gui(tmp_path, monkeypatch, game_watch=False)
    try:
        monkeypatch.setattr(QFileDialog, "getOpenFileName",
                            staticmethod(lambda *a, **k: ("C:/Users/x/AppData/Roaming/Spotify/Spotify.exe", "")))
        monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))
        w.exclude_app()
        st = settings.load_settings()
        assert st["apps_exclude"] == ["spotify.exe"] and st["game_watch"] and w.watcher
        assert gamewatch.web_client(r"C:\Users\x\AppData\Roaming\Spotify\Spotify.exe", st)   # its 443 nets: excluded
        assert not gamewatch.classify(r"C:\Users\x\AppData\Roaming\Spotify\Spotify.exe", st)  # but not a game
        w.list_combo.setCurrentIndex(w.list_combo.findData("apps_exclude"))
        assert w.list_edit.toPlainText() == "spotify.exe\n"
        w.list_edit.setPlainText("")
        w.save_list()
        assert settings.load_settings()["apps_exclude"] == []
    finally:
        w.stop_watcher()
        _close(app, w)

def test_autostart_task_has_no_time_limit():
    pytest.importorskip("PySide6")
    from app.gui.window import MainWindow
    ps = MainWindow.autostart_command()[-1]
    assert "-ExecutionTimeLimit ([TimeSpan]::Zero)" in ps        # schtasks default: killed after 72 h
    assert "-AllowStartIfOnBatteries" in ps and "-DontStopIfGoingOnBatteries" in ps
    assert "-RunLevel Highest" in ps and "-AtLogOn" in ps and "--tray" in ps

def test_gui_network_change_restores_its_strategy(tmp_path, monkeypatch):
    mobile = {"base": "ALT3", "picks": {}}
    app, w = _gui(tmp_path, monkeypatch, net_asn="AS100",
                  networks={"AS200": {"isp": "Mobile", "choice": mobile, "time": 1}})
    try:
        monkeypatch.setattr(tester, "vpn_active", lambda: None)
        monkeypatch.setattr(w, "_run", lambda fn, done, line=None: done(fn(lambda m: None)))
        started = []
        monkeypatch.setattr(w, "start_runner", lambda: started.append(1) or True)
        monkeypatch.setattr(type(w.runner), "running", property(lambda self: True))
        monkeypatch.setattr(sharedb, "isp", lambda: ("AS200", "Mobile"))
        w._network_changed()
        assert tester.load_settings()["strategy"] == mobile and started    # known network: its pick, no re-pick
        monkeypatch.setattr(sharedb, "isp", lambda: ("AS300", "Cafe"))
        w._network_changed()
        assert w._new_net and tester.load_settings()["net_asn"] == "AS300"  # unknown: may re-pick without the 6 h pause
    finally:
        _close(app, w)

def test_gui_isp_line(tmp_path, monkeypatch):
    app, w = _gui(tmp_path, monkeypatch)
    try:
        monkeypatch.setattr(tester, "vpn_active", lambda: None)
        monkeypatch.setattr(w, "_run", lambda fn, done, line=None: done(fn(lambda m: None)))
        monkeypatch.setattr(sharedb, "isp", lambda: ("AS100", "Home ISP"))
        monkeypatch.setattr(sharedb, "isp_entry",
                            lambda a, v: {"votes": 3, "reports": 4, "choice": {"base": "ALT8", "picks": {}}})
        w.fill_isp()
        assert "Home ISP" in w.lbl_isp.text() and "отчётов: 4" in w.lbl_isp.text() and "та же" in w.lbl_isp.text()
        monkeypatch.setattr(sharedb, "isp_entry", lambda a, v: None)  # base unreachable (GitHub down)
        w.fill_isp()
        assert w.lbl_isp.text() == "Провайдер: Home ISP"
    finally:
        _close(app, w)

def test_gui_quiet_updates_defer_for_a_day_at_most(tmp_path, monkeypatch):
    from app import quiet
    app, w = _gui(tmp_path, monkeypatch)
    try:
        monkeypatch.setattr(quiet, "busy_reason", lambda game=False: "идёт звонок (discord)")
        assert not w._update_deferred(1000.0)  # setting off: never waits
        w.save(quiet_updates=True)
        assert w._update_deferred(1000.0) and w._next_update_check == 1600.0
        assert w._update_deferred(50000.0)
        assert not w._update_deferred(1000.0 + 86400)  # a day of waiting: update anyway
        monkeypatch.setattr(quiet, "busy_reason", lambda game=False: None)
        assert not w._update_deferred(200000.0) and not w._deferred_since
    finally:
        _close(app, w)

def test_gui_tray_strategy_and_games(tmp_path, monkeypatch):
    app, w = _gui(tmp_path, monkeypatch)
    try:
        w._fill_tray_strategies()
        acts = w.menu_strat.actions()
        assert acts[0].text().startswith("Авто") and len(acts) == w.combo.count()
        monkeypatch.setattr(w, "start_runner", lambda: True)
        acts[1].trigger()
        assert w.settings["strategy"]["base"] == w.combo.itemText(1) and w.combo.currentIndex() == 1
        w._fill_tray_games()
        assert [a.text() for a in w.menu_games.actions()] == ["Слежение за играми выключено"]
        w.save(game_watch=True, games_exclude=["old.exe"])
        w._fill_tray_games()
        a = w.menu_games.actions()[0]
        assert not a.isChecked() and "вне обхода" in a.text()
        a.trigger()  # check it again: the game is back in the bypass
        assert w.settings["games_exclude"] == []
    finally:
        _close(app, w)

def test_gui_game_exclusion_checkbox(tmp_path, monkeypatch):
    """The «Обход» checkbox: off keeps the game in the list, out of the bypass (and out of winws on restart)."""
    app, w = _gui(tmp_path, monkeypatch, game_watch=True)
    try:
        calls = []
        class _W:
            def games(self):
                return {"g.exe": {"path": "", "ips": {"9.9.9.9"}, "active": False, "ping_ms": None}}
            def set_excluded(self, exe, on):
                calls.append((exe, on))
        w.watcher = _W()
        w.runner.stop = lambda: None
        w.fill_games()
        from PySide6.QtCore import Qt
        assert w.table.rowCount() == 1 and w.table.item(0, 4).checkState() == Qt.Checked
        w.table.item(0, 4).setCheckState(Qt.Unchecked)      # the user unticked «Обход»
        st = settings.load_settings()
        assert st["games_exclude"] == ["g.exe"] and st["nocapture"]["g.exe"] == ["9.9.9.9"]
        assert calls == [("g.exe", True)]
        assert w.table.item(0, 3).text() == "вне обхода" and w.table.rowCount() == 1  # still listed
        w.table.item(0, 4).setCheckState(Qt.Checked)
        st = settings.load_settings()
        assert st["games_exclude"] == [] and "g.exe" not in (st.get("nocapture") or {})
    finally:
        _close(app, w)

def test_gui_game_capture_off_disables_the_game_filter(tmp_path, monkeypatch):
    """Global switch off: no game ports in the filter, every game shown as «вне обхода», boxes disabled."""
    app, w = _gui(tmp_path, monkeypatch, game_capture=False, game_watch=True)
    try:
        seen = []
        class _W:
            def games(self):
                return {"g.exe": {"path": "", "ips": {"9.9.9.9"}, "active": True, "ping_ms": None}}
            def set_capture(self, on):
                seen.append(on)
        w.watcher = _W()
        assert w._game_flags() == (False, False)
        from PySide6.QtCore import Qt
        w.fill_games()
        assert w.table.item(0, 3).text() == "вне обхода"
        assert w.table.item(0, 4).checkState() == Qt.Unchecked
        assert not w.table.item(0, 4).flags() & Qt.ItemIsEnabled  # nothing to toggle per game
        w._fill_tray_games()
        assert [a.text() for a in w.menu_games.actions()] == ["Перехват игр выключен в настройках"]
        w.on_check("game_capture", True)                          # switch back on
        assert seen == [True] and w._game_flags() == (True, True)  # the found game enables the filter again
    finally:
        _close(app, w)

def test_gui_weekly_ipset_update_runs_once(tmp_path, monkeypatch):
    app, w = _gui(tmp_path, monkeypatch)
    try:
        w.save(ipset_updated=0)
        monkeypatch.setattr(w, "_run", lambda fn, done, line=None: done(fn(lambda m: None)))
        calls = []
        monkeypatch.setattr(lists, "update_ipset",
                            lambda log: calls.append(1) or settings.save_settings(ipset_updated=time.time()) or True)
        monkeypatch.setattr(tester, "egress_ip", lambda: None)
        w._tick()
        w._ipset_try = 0                                     # an hour later
        w._tick()
        assert calls == [1]                                  # the saved time counts, not the window's stale copy
    finally:
        _close(app, w)


def test_site_check_restores_bind(monkeypatch):
    """Проверка сайта идёт мимо VPN, но BIND не должен остаться для следующих проб и диагностики."""
    from app import vpn
    from app.gui import window
    monkeypatch.setattr(tester, "vpn_active", lambda: "VPN")
    monkeypatch.setattr(vpn, "bind_ip", lambda: "192.168.1.5")
    tester.BIND = "10.0.0.9"
    try:
        assert window._with_direct_path(lambda: tester.BIND) == "192.168.1.5"
        assert tester.BIND == "10.0.0.9"
        with pytest.raises(RuntimeError):
            window._with_direct_path(lambda: (_ for _ in ()).throw(RuntimeError("boom")))
        assert tester.BIND == "10.0.0.9"
    finally:
        tester.BIND = None


def test_site_check_without_bypass_explains():
    from app.gui import window
    assert "включите обход" in window._site_check("example.com", False)
