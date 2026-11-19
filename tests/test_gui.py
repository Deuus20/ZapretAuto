"""Тесты окна и трея: поднимают QApplication и MainWindow."""
import time

import pytest
from helpers import _close, _gui

from app import diag, dns, gamewatch, lists, paths, settings, sharedb, tester, updater, whitelist, zapret2


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

def test_gui_adds_a_user_preset(tmp_path, monkeypatch):
    """«Добавить пресет»: файл проверяется, копируется в data/presets и сразу виден в списке стратегий."""
    pytest.importorskip("PySide6")
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setattr(paths, "DATA", tmp_path)
    monkeypatch.setattr(paths, "LISTS", tmp_path / "lists")
    monkeypatch.setattr(paths, "PRESETS", tmp_path / "presets")
    settings._cache = None
    from PySide6.QtWidgets import QApplication, QFileDialog
    app = QApplication.instance() or QApplication([])
    from app.gui.window import MainWindow
    src = next(paths.VENDOR.glob("general.bat")).read_text(encoding="utf-8")
    f = tmp_path / "general (MY).bat"
    f.write_text(src, encoding="utf-8")
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(f), ""))
    w = MainWindow()
    try:
        w.tray.showMessage = lambda *a, **k: None
        w.add_preset()
        assert w.combo.findText("свой: MY") >= 0
        assert (paths.PRESETS / "MY.bat").read_text(encoding="utf-8") == src
    finally:
        w.tray.hide()
        w._timer.stop()
        w.deleteLater()
        app.processEvents()

def test_mix_rows_are_not_squeezed():
    """Строки групп получают свою высоту: карточка больше не сжимается и текст не наезжает."""
    pytest.importorskip("PySide6")
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from app.gui.mix_page import MixPage
    page = MixPage()
    page.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    page.resize(900, 620)
    page.show()
    try:
        page.reload("ALT8")
        app.processEvents()
        assert page._rows
        for _group, cb in page._rows:
            row = cb.parentWidget()
            assert row.height() >= row.minimumSizeHint().height()
        assert page.presets_box.isHidden()  # пустая карточка «Мои пресеты» не рисует полоску
    finally:
        page.deleteLater()
        app.processEvents()


def test_home_after_revert_keeps_the_old_order(tmp_path, monkeypatch):
    """Главная как была: прогресс скрыт, плашки отдельной строкой, «?» только у комбинации."""
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
        w.runner.stop()
        assert w.progress.isHidden()
        assert w.chips.parentWidget() is w.tabs.widget(0)  # плашки отдельной строкой страницы, а не в шапке
        w.save(strategy={"base": "ALT8", "picks": {}})
        w.refresh()
        assert w.btn_desc_help.isHidden()  # полное имя совпадает с коротким
        w.save(strategy={"base": "ALT8", "picks": {"tcp:443:list-google": "FAKE TLS AUTO ALT"}})
        w.refresh()
        assert not w.btn_desc_help.isHidden()  # у комбинации есть что показать
    finally:
        w.tray.hide()
        w._timer.stop()
        w.deleteLater()
        app.processEvents()

def test_checks_box_does_not_save_on_load(tmp_path, monkeypatch):
    """Загрузка карточки проверок не считается правкой: иначе при каждом открытии шло бы сохранение."""
    pytest.importorskip("PySide6")
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setattr(paths, "DATA", tmp_path)
    monkeypatch.setattr(paths, "LISTS", tmp_path / "lists")
    settings._cache = None
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from app.gui.checks_box import ChecksBox
    settings.save_settings(checks={"hidden": ["Скорость YouTube"], "custom": [
        {"name": "x.example", "url": "https://x.example"}]})
    page = ChecksBox()
    try:
        got = []
        page.changed.connect(got.append)
        page.load()
        assert got == []  # ни одного «сохранения» от загрузки
        assert not page.boxes["Скорость YouTube"].isChecked()
        assert len(page._rows) == 1
    finally:
        page.deleteLater()
        app.processEvents()

def test_check_access_does_not_run_twice(tmp_path, monkeypatch):
    """Второй запрос проверки во время прогона не запускает второй: он ждёт и повторяется после."""
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
        w.runner.stop()
        started = []
        monkeypatch.setattr(w, "_run", lambda fn, done, line=None: started.append(done))
        w.check_access()
        w.check_access()  # пока идёт первый прогон
        assert len(started) == 1 and w._check_pending
        started[0]({"Discord": True})  # прогон закончился
        assert len(started) == 2 and not w._check_pending
    finally:
        w.tray.hide()
        w._timer.stop()
        w.deleteLater()
        app.processEvents()

def test_gui_deleted_preset_base_resets_strategy(tmp_path, monkeypatch):
    """Удалили пресет, который был основой: стратегия очищается, ссылки на файл не остаётся."""
    pytest.importorskip("PySide6")
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setattr(paths, "DATA", tmp_path)
    monkeypatch.setattr(paths, "LISTS", tmp_path / "lists")
    monkeypatch.setattr(paths, "PRESETS", tmp_path / "presets")
    settings._cache = None
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from app import strategies
    from app.gui.window import MainWindow
    by = {s.name: s for s in strategies.load_all()}
    saved = strategies.save_preset("основа", strategies.mix("x", {}, by["ALT8"]))
    w = MainWindow()
    try:
        w.runner.stop()
        w.save(strategy={"base": saved, "picks": {}})
        w.on_mix_deleted(saved)
        assert not w.settings.get("strategy")  # выбирать нечего: прошлой комбинации нет
    finally:
        w.tray.hide()
        w._timer.stop()
        w.deleteLater()
        app.processEvents()

def test_diag_hint_hides_after_the_run(tmp_path, monkeypatch):
    """Подсказка «Пока не проверяли» исчезает после прогона, таблица видна всегда и не ломает раскладку."""
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
        w.runner.stop()
        assert not w.diag_hint.isHidden() and not w.diag_table.isHidden()
        w.on_diag_done([{"label": "BFE", "level": "ok", "detail": "служба: Running", "fix": None}])
        assert w.diag_hint.isHidden() and not w.diag_table.isHidden()
    finally:
        w.tray.hide()
        w._timer.stop()
        w.deleteLater()
        app.processEvents()

def test_mix_counter_and_reset():
    """Внизу микса видно, сколько групп заменено, и замены можно сбросить одной кнопкой."""
    pytest.importorskip("PySide6")
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from app.gui.mix_page import MixPage
    page = MixPage()
    try:
        page.reload("ALT8")
        total = len(page._rows)
        assert page.lbl_changed.text() == f"заменено 0 из {total}"
        assert not page.btn_reset.isEnabled()
        group, cb = next((g, c) for g, c in page._rows if c.count() > 1)
        cb.setCurrentIndex(1)
        assert page.lbl_changed.text() == f"заменено 1 из {total}"
        assert page.btn_reset.isEnabled()
        page._reset()
        assert page.lbl_changed.text() == f"заменено 0 из {total}"
        assert all(c.currentIndex() == 0 for _g, c in page._rows)
    finally:
        page.deleteLater()
        app.processEvents()

def test_gui_report_menu_has_both_actions(tmp_path, monkeypatch):
    """«Отчёт…» - одно меню на два действия: zip-файл и страница отправки. Отдельной кнопки нет."""
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
        w.runner.stop()
        assert [a.text() for a in w._report_actions().actions()] == ["Сохранить в zip…", "Отправить разработчику…"]
        assert not hasattr(w, "btn_send")
        assert w.btn_report.text() == "Отчёт…"
    finally:
        w.tray.hide()
        w._timer.stop()
        w.deleteLater()
        app.processEvents()

def test_mix_name_lives_in_the_preset_card():
    """Имя пресета и основа - строки одной карточки, а не поле, висящее на фоне страницы."""
    pytest.importorskip("PySide6")
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QWidget
    app = QApplication.instance() or QApplication([])
    from app.gui.mix_page import MixPage
    page = MixPage()

    def card_of(widget) -> QWidget | None:
        p = widget.parentWidget()
        while p is not None and p.objectName() != "card":
            p = p.parentWidget()
        return p
    try:
        assert card_of(page.name) is not None
        assert card_of(page.name) is card_of(page.base)  # обе строки в карточке «Пресет»
    finally:
        page.deleteLater()
        app.processEvents()

def test_gui_missing_preset_file_is_dropped_from_the_list(tmp_path, monkeypatch):
    """Пресет удалили мимо приложения: список перечитывается, мёртвая ссылка в настройках снимается."""
    pytest.importorskip("PySide6")
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setattr(paths, "DATA", tmp_path)
    monkeypatch.setattr(paths, "LISTS", tmp_path / "lists")
    monkeypatch.setattr(paths, "PRESETS", tmp_path / "presets")
    settings._cache = None
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from app import strategies
    from app.gui.window import MainWindow
    by = {s.name: s for s in strategies.load_all()}
    saved = strategies.save_preset("пропавший", strategies.mix("x", {}, by["ALT8"]))
    w = MainWindow()
    try:
        w.runner.stop()
        w.save(strategy={"base": saved, "picks": {}})
        (paths.PRESETS / "пропавший.bat").unlink()  # удалили в проводнике, приложение не знает
        w.reload_strategies()
        assert w.combo.findText(saved) < 0
        assert not w.settings.get("strategy")  # мёртвая ссылка снята, а не осталась в настройках
    finally:
        w.tray.hide()
        w._timer.stop()
        w.deleteLater()
        app.processEvents()

def test_mix_page_collects_picks_from_another_strategy():
    """Страница «Микс»: группа по умолчанию остаётся из основы, выбранная берётся из другой стратегии."""
    pytest.importorskip("PySide6")
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from app.gui.mix_page import MixPage
    page = MixPage()
    try:
        page.reload("ALT8")
        assert page.base.currentText() == "ALT8" and page.name.text().startswith("микс ")
        group, cb = next((g, c) for g, c in page._rows if c.count() > 1)
        cb.setCurrentIndex(1)
        picks = {g: c.currentData() for g, c in page._rows if c.currentData()}
        assert picks == {group: cb.currentData()}
    finally:
        page.deleteLater()
        app.processEvents()

def test_gui_mix_page_saves_a_preset(tmp_path, monkeypatch):
    pytest.importorskip("PySide6")
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setattr(paths, "DATA", tmp_path)
    monkeypatch.setattr(paths, "LISTS", tmp_path / "lists")
    monkeypatch.setattr(paths, "PRESETS", tmp_path / "presets")
    settings._cache = None
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from app.gui.window import MainWindow
    w = MainWindow()
    try:
        w.runner.stop()
        page = w.page_mix
        page.reload("ALT8")
        group, cb = next((g, c) for g, c in page._rows if c.count() > 1)
        cb.setCurrentIndex(1)
        page.name.setText("тест микс")
        page._save()
        saved = w.settings["strategy"]["base"]
        assert saved == "свой: тест микс" and w.combo.findText(saved) >= 0
        assert (paths.PRESETS / "тест микс.bat").exists()
    finally:
        w.tray.hide()
        w._timer.stop()
        w.deleteLater()
        app.processEvents()

def test_checks_page_hides_and_adds_addresses():
    """Своя строка: тип выбирается списком, адрес и порт вводятся как обычные поля, без tcp:// вручную."""
    pytest.importorskip("PySide6")
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from app.gui.checks_box import ChecksBox
    page = ChecksBox()
    try:
        page.boxes["Скорость YouTube"].setChecked(False)
        page.add_row("site", "example.com", "")
        cfg, err = page.result_checks()
        assert err == "" and cfg["hidden"] == ["Скорость YouTube"]
        assert {"name": "example.com", "url": "https://example.com"} in cfg["custom"]
        page.add_row("tcp", "192.168.1.10", "27015")
        cfg, err = page.result_checks()
        assert {"name": "192.168.1.10:27015", "url": "tcp://192.168.1.10:27015"} in cfg["custom"]
        page.add_row("tcp", "192.168.1.11", "99999")  # порт вне диапазона
        cfg, err = page.result_checks()
        assert "порт" in err and cfg["custom"] == [{"name": "example.com", "url": "https://example.com"},
                                                   {"name": "192.168.1.10:27015", "url": "tcp://192.168.1.10:27015"}]
    finally:
        page.deleteLater()
        app.processEvents()

def test_checks_page_saves_toggles_with_a_broken_row():
    """Ошибка в одной строке не отменяет галочки: иначе человек снял её, а сохраниться она не успела."""
    pytest.importorskip("PySide6")
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from app.gui.checks_box import ChecksBox
    page = ChecksBox()
    try:
        page.add_row("tcp", "10.0.0.1", "")  # порт ещё не вписан
        got = []
        page.changed.connect(got.append)
        page.boxes["Скорость YouTube"].setChecked(False)
        assert got and "Скорость YouTube" in got[-1]["hidden"]
        assert "порт" in page.status.text()
    finally:
        page.deleteLater()
        app.processEvents()

def test_checks_page_keeps_the_last_good_address():
    """Недописанный порт не стирает уже сохранённый адрес и не сохраняет тот же конфиг второй раз."""
    pytest.importorskip("PySide6")
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from app.gui.checks_box import ChecksBox
    page = ChecksBox()
    try:
        page.add_row("tcp", "10.0.0.1", "27015")
        page._changed()
        cfg, err = page.result_checks()
        assert err == "" and cfg["custom"] == [{"name": "10.0.0.1:27015", "url": "tcp://10.0.0.1:27015"}]
        got = []
        page.changed.connect(got.append)
        row = page._rows[0]
        row[3].setText("")  # порт стёрли: строка недописана
        page._changed()
        assert got == []  # конфиг не изменился: присылать его второй раз незачем
        assert page.result_checks()[0]["custom"] == cfg["custom"]  # прошлый адрес остался
        assert "порт" in page.status.text()
    finally:
        page.deleteLater()
        app.processEvents()

def test_checks_page_splits_a_stored_url():
    """Адрес без порта из ручной правки settings.json не превращается в строку с портом «host»."""
    pytest.importorskip("PySide6")
    from app.gui.checks_box import ChecksBox
    assert ChecksBox._split("tcp://example.com") == ("tcp", "example.com", "")
    assert ChecksBox._split("stun://example.com:3478") == ("stun", "example.com", "3478")
    assert ChecksBox._split("https://example.com/x") == ("site", "example.com/x", "")

def test_gui_checks_apply_without_a_save_button(tmp_path, monkeypatch):
    """Галочки и строки применяются сразу, но проверка доступа одна на серию правок."""
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
        w.runner.stop()
        calls = []
        monkeypatch.setattr(w, "check_access", lambda *a, **k: calls.append(1))
        page = w.checks_box  # карточка в «Настройках», а не отдельная вкладка
        page.boxes["Telegram (веб)"].setChecked(False)  # галочка сохраняется сразу
        page.add_row("site", "x.example", "")
        page._changed()  # то же, что делает таймер после ввода адреса
        assert w.settings["checks"]["custom"] == [{"name": "x.example", "url": "https://x.example"}]
        assert "Telegram (веб)" in w.settings["checks"]["hidden"]
        assert calls == [] and w._checks_timer.isActive()  # иначе полный прогон на каждый дописанный символ
        w._checks_timer.timeout.emit()  # пауза ввода закончилась
        assert calls
    finally:
        w.tray.hide()
        w._timer.stop()
        w.deleteLater()
        app.processEvents()

def test_gui_mix_page_deletes_own_preset(tmp_path, monkeypatch):
    """На странице «Микс» свой пресет можно удалить, и файл действительно исчезает."""
    pytest.importorskip("PySide6")
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setattr(paths, "DATA", tmp_path)
    monkeypatch.setattr(paths, "LISTS", tmp_path / "lists")
    monkeypatch.setattr(paths, "PRESETS", tmp_path / "presets")
    settings._cache = None
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from app import strategies
    from app.gui.window import MainWindow
    by = {s.name: s for s in strategies.load_all()}
    saved = strategies.save_preset("на удаление", strategies.mix("x", {}, by["ALT8"]))
    w = MainWindow()
    try:
        w.runner.stop()
        from PySide6.QtWidgets import QMessageBox
        monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
        w.page_mix._delete(saved)
        assert not (paths.PRESETS / "на удаление.bat").exists()
        assert w.combo.findText(saved) < 0
    finally:
        w.tray.hide()
        w._timer.stop()
        w.deleteLater()
        app.processEvents()

def test_gui_preset_delete_asks_first(tmp_path, monkeypatch):
    """Случайный клик по «Удалить» не должен уносить файл: сначала вопрос, «Нет» ничего не удаляет."""
    pytest.importorskip("PySide6")
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setattr(paths, "DATA", tmp_path)
    monkeypatch.setattr(paths, "LISTS", tmp_path / "lists")
    monkeypatch.setattr(paths, "PRESETS", tmp_path / "presets")
    settings._cache = None
    from PySide6.QtWidgets import QApplication, QMessageBox
    app = QApplication.instance() or QApplication([])
    from app import strategies
    from app.gui.window import MainWindow
    by = {s.name: s for s in strategies.load_all()}
    saved = strategies.save_preset("не удалять", strategies.mix("x", {}, by["ALT8"]))
    w = MainWindow()
    try:
        w.runner.stop()
        monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.No)
        w.page_mix._delete(saved)
        assert (paths.PRESETS / "не удалять.bat").exists()
    finally:
        w.tray.hide()
        w._timer.stop()
        w.deleteLater()
        app.processEvents()

def test_mix_page_does_not_restore_a_reset_group():
    """«Как в основе» не должно возвращаться после переключения вкладок: выбор человека важнее прошлого."""
    pytest.importorskip("PySide6")
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from app.gui.mix_page import MixPage
    page = MixPage()
    try:
        page.reload("ALT8")
        group, cb = next((g, c) for g, c in page._rows if c.count() > 1)
        cb.setCurrentIndex(1)
        chosen = cb.currentText()
        page.reload_keep()  # ушли на другую вкладку и вернулись
        assert next(c for g, c in page._rows if g == group).currentText() == chosen
        next(c for g, c in page._rows if g == group).setCurrentIndex(0)  # вернули «как в основе»
        page.reload_keep()
        assert next(c for g, c in page._rows if g == group).currentText() == "как в основе"
        page.reload("ALT8", None)  # открыли страницу с главной, сохранённых замен нет
        assert next(c for g, c in page._rows if g == group).currentText() == "как в основе"
    finally:
        page.deleteLater()
        app.processEvents()

def test_gui_deleted_preset_leaves_the_combination(tmp_path, monkeypatch):
    """Удалённый пресет был заменой группы: ссылка на него не должна остаться ни в стратегии, ни в статусе."""
    pytest.importorskip("PySide6")
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setattr(paths, "DATA", tmp_path)
    monkeypatch.setattr(paths, "LISTS", tmp_path / "lists")
    monkeypatch.setattr(paths, "PRESETS", tmp_path / "presets")
    settings._cache = None
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from app import strategies
    from app.gui.window import MainWindow
    by = {s.name: s for s in strategies.load_all()}
    saved = strategies.save_preset("замена", strategies.mix("x", {}, by["ALT8"]))
    w = MainWindow()
    try:
        w.runner.stop()
        w.save(strategy={"base": "ALT8", "picks": {"tcp:443:list-google": saved}})
        w.on_mix_deleted(saved)
        c = w.settings["strategy"]
        assert c["base"] == "ALT8" and c["picks"] == {}  # группа снова берётся из основы
        assert saved not in w.describe_full()
    finally:
        w.tray.hide()
        w._timer.stop()
        w.deleteLater()
        app.processEvents()

def test_chips_wrap_instead_of_running_off_the_card():
    """Свои проверки добавляют плашки: они переносятся на следующую строку, а не уезжают за край."""
    pytest.importorskip("PySide6")
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QWidget
    app = QApplication.instance() or QApplication([])
    from app.gui.widgets import FlowLayout
    box = QWidget()
    flow = FlowLayout(box)
    for _ in range(3):
        chip = QWidget(box)
        chip.setFixedSize(100, 20)
        flow.addWidget(chip)
    assert flow.heightForWidth(1000) == 20  # всё в одну строку
    assert flow.heightForWidth(250) == 46  # две строки: 20 + 6 + 20
    assert flow.heightForWidth(150) == 72  # три: в строку влезает только одна плашка
    box.deleteLater()
    app.processEvents()

def test_rail_label_shrinks_instead_of_clipping():
    """«Диагностика» не влезала в рейл: подпись уменьшается, а не обрезается краем окна."""
    pytest.importorskip("PySide6")
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtGui import QFont, QFontMetrics
    from PySide6.QtWidgets import QApplication
    _app = QApplication.instance() or QApplication([])  # держим ссылку: без неё Qt может освободить приложение
    from app.gui.widgets import fit_font
    f = QFont()
    f.setPixelSize(11)
    fitted = fit_font(f, "Диагностика", 90)
    assert QFontMetrics(fitted).horizontalAdvance("Диагностика") <= 90
    assert 8 <= fitted.pixelSize() <= 11
    assert fit_font(f, "Игры", 90).pixelSize() == 11  # короткая подпись не мельчает

def test_long_combo_lists_are_capped_and_scroll():
    """Длинный список стратегий не разворачивается на весь экран: не больше 12 строк и прокрутка."""
    pytest.importorskip("PySide6")
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QComboBox
    _app = QApplication.instance() or QApplication([])
    from app.gui.widgets import fit_combo
    cb = fit_combo(QComboBox())
    assert cb.maxVisibleItems() == 12
    assert cb.view() is not None and cb.view().textElideMode() == Qt.TextElideMode.ElideRight

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
        monkeypatch.setattr(w, "_run", lambda fn, done, line=None: done(fn(lambda m: None)))
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
        monkeypatch.setattr(tester, "quick_check", lambda light=False, speed=False: {"Discord": False, "YouTube": True})
        w.runner.proc = type("P", (), {"poll": lambda self: None})()          # looks running
        w._health()
        assert not w.busy and w._health_fails == 0
        # zapret2 не справился: приложение само вернулось к обычному движку
        assert not w.cb["zapret2"].isChecked() and settings.load_settings()["zapret2"] is False
        assert started[-1] == "ALT8" and w.combo.isEnabled()
    finally:
        _close(app, w)

def test_gui_diag_finds_whitelist(tmp_path, monkeypatch):
    """Белые списки диагностика находит сама: отдельной кнопки больше нет."""
    app, w = _gui(tmp_path, monkeypatch)
    try:
        monkeypatch.setattr(w, "_run", lambda fn, done, line=None: done(fn(lambda m: None)))
        monkeypatch.setattr(diag, "run_checks", lambda: [{"label": "Служба zapret", "level": "ok",
                                                          "detail": "работает", "fix": None}])
        monkeypatch.setattr(whitelist, "check", lambda: {"verdict": "whitelist"})
        state = {"on": False}
        monkeypatch.setattr(type(w.runner), "running", property(lambda self: state["on"]))
        monkeypatch.setattr(w.runner, "stop", lambda: state.update(on=False))
        monkeypatch.setattr(w, "start_runner", lambda: state.update(on=True))
        w.run_diag()
        labels = [r["label"] for r in w._last_diag]
        assert "Белые списки" in labels
        row = next(r for r in w._last_diag if r["label"] == "Белые списки")
        assert row["level"] == "err" and "белые списки" in row["detail"].lower()
    finally:
        _close(app, w)

def test_gui_describe_names_the_strategy_mix(tmp_path, monkeypatch):
    """Вместо «авто-комбинация из N стратегий»: коротко в строке статуса, полностью - в подсказке."""
    app, w = _gui(tmp_path, monkeypatch)
    try:
        from app.gui.window_pages import short_name
        assert short_name("FAKE TLS AUTO ALT") == "FTAA"
        assert short_name("FAKE TLS AUTO ALT2") == "FTAA2"  # варианты не сливаются
        assert short_name("ALT8") == "ALT8" and short_name("ALT 2") == "ALT2"
        w.settings["strategy"] = {"base": "ALT8", "picks": {
            "tcp:443:list-google": "ALT11",
            "udp:19294-19344,50000-50100:discord,stun": "ALT11",
            "tcp:80,443:list-general": "FAKE TLS AUTO ALT"}}
        assert w.describe() == "комбинация ALT8+ALT11+FTAA"
        assert w.describe_full() == ("комбинация ALT8 + ALT11 (Google, голос Discord) + "
                                     "FAKE TLS AUTO ALT (сайты)")
    finally:
        _close(app, w)

def test_gui_one_question_mark_for_the_mix(tmp_path, monkeypatch):
    """«?» стоит у комбинации в строке статуса и делает то же, что наведение: ни окна, ни второго «?»."""
    app, w = _gui(tmp_path, monkeypatch)
    try:
        from PySide6.QtWidgets import QToolTip
        w.settings["strategy"] = {"base": "ALT8", "picks": {"tcp:443:list-google": "ALT11"}}
        w.refresh()
        full = "комбинация ALT8 + ALT11 (Google)"
        assert w.describe_full() == full
        assert not w.btn_desc_help.isHidden() and w.btn_desc_help.toolTip() == full
        assert not hasattr(w, "btn_strategy_help")  # старый «?» из карточки убран
        shown = []
        monkeypatch.setattr(QToolTip, "showText", lambda *a, **k: shown.append(a[1]))
        w.show_strategy_help()
        assert shown == [full]  # по нажатию та же подсказка, а не окно
        w.settings["strategy"] = {"base": "ALT8", "picks": {}}
        w.refresh()
        assert w.btn_desc_help.isHidden()  # у одиночной стратегии подсказке нечего добавить
    finally:
        _close(app, w)

def test_gui_repairs_windivert_service_then_gives_the_reboot_advice(tmp_path, monkeypatch):
    """winws не открыл драйвер (1058): программа чинит службу и повторяет запуск, второй раз за 10 минут не чинит."""
    app, w = _gui(tmp_path, monkeypatch)
    try:
        from app import diag
        from app import runner as runner_mod
        monkeypatch.setattr(runner_mod, "last_output",
                            lambda lines=20: "windivert: error opening filter: The service cannot be started, "
                                             "either because it is disabled")
        calls = []
        monkeypatch.setattr(diag, "repair_windivert",
                            lambda: calls.append(1) or (True, "Служба WinDivert запущена."))
        started = []
        monkeypatch.setattr(w, "start_runner", lambda: started.append(1) or True)
        monkeypatch.setattr(w.tray, "showMessage", lambda *a: None)
        monkeypatch.setattr(w, "_run", lambda fn, done, line=None: done(fn(None)))
        w._explain_failure()
        assert calls and started and w.error == ""
        w._explain_failure()  # сразу второй раз: починка не помогает, советуем перезагрузку
        assert len(calls) == 1 and "ерезагруз" in w.error and not w._expect_running
    finally:
        _close(app, w)

def test_gui_games_merged_and_empty_hidden(tmp_path, monkeypatch):
    """Одна игра = лаунчер и клиент: одна строка; игры без серверов не показываем, пинг без данных - «—»."""
    app, w = _gui(tmp_path, monkeypatch, game_watch=True)
    try:
        base = r"D:\Steam\steamapps\common\Wardogs"
        class _W:
            def games(self):
                return {
                    base + r"\launcher.exe": {"path": base + r"\launcher.exe", "ips": set(), "active": False,
                                              "ping_ms": None},
                    base + r"\client-win64-shipping.exe": {"path": base + r"\client-win64-shipping.exe",
                                                           "ips": {"9.9.9.9", "8.8.8.8"}, "active": True,
                                                           "ping_ms": None},
                    r"D:\Games\other.exe": {"path": r"D:\Games\other.exe", "ips": set(), "active": False,
                                            "ping_ms": None},
                }
        w.watcher = _W()
        w.fill_games()
        assert w.table.rowCount() == 1
        assert w.table.item(0, 0).text() == "Wardogs" and w.table.item(0, 1).text() == "2"
        assert w.table.item(0, 2).text() == "—" and w.table.item(0, 3).text() == "играете"
    finally:
        _close(app, w)

def test_gui_notifications_can_be_disabled(tmp_path, monkeypatch):
    app, w = _gui(tmp_path, monkeypatch)
    try:
        calls = []
        monkeypatch.setattr(w.tray, "showMessage", lambda *a: calls.append(a))
        w.notify("тест")
        assert calls
        w.save(notifications=False)
        w.notify("ещё одно")
        assert len(calls) == 1  # выключены: только журнал
    finally:
        _close(app, w)

def test_gui_dns_card(tmp_path, monkeypatch):
    """Карточка DNS: авто-подбор, свой адрес, проверка ввода. netsh и сеть подменены."""
    from PySide6.QtWidgets import QMessageBox
    app, w = _gui(tmp_path, monkeypatch)
    try:
        shown, warned = [], []
        monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: shown.append(a[2])))
        monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: warned.append(a[2])))
        monkeypatch.setattr(w, "_run", lambda fn, done, line=None: done(fn(lambda m: None)))
        monkeypatch.setattr(dns, "best", lambda log=print: {"name": "Cloudflare", "status": "ok",
                                                            "ips": ("1.1.1.1", "1.0.0.1")})
        monkeypatch.setattr(dns, "apply", lambda ips: (True, ", ".join(ips) + " на адаптере Ethernet"))
        w.dns_combo.setCurrentIndex(w.dns_combo.findData("auto"))
        w.dns_apply()
        assert shown and "Cloudflare" in shown[-1]
        assert settings.load_settings()["dns_servers"] == ["1.1.1.1", "1.0.0.1"]
        w.dns_combo.setCurrentIndex(w.dns_combo.findData("custom"))
        w.dns_edit.setText("8.8.8.8, 1.1.1.1")
        w.dns_apply()
        assert settings.load_settings()["dns_servers"] == ["8.8.8.8", "1.1.1.1"]
        w.dns_edit.setText("не адрес")
        w.dns_apply()
        assert warned and settings.load_settings()["dns_servers"] == ["8.8.8.8", "1.1.1.1"]  # ничего не сломали
    finally:
        _close(app, w)

def test_gui_no_pick_says_whitelist(tmp_path, monkeypatch):
    """Подбор не помог и проверка увидела белые списки: советуем VPN, а не крутим подбор снова."""
    from PySide6.QtWidgets import QMessageBox
    app, w = _gui(tmp_path, monkeypatch)
    try:
        shown, started = [], []
        monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: shown.append(a[2])))
        monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.No))
        monkeypatch.setattr(w, "_run", lambda fn, done, line=None: done(fn(lambda m: None)))
        monkeypatch.setattr(whitelist, "check", lambda: {"verdict": "whitelist"})
        monkeypatch.setattr(tester, "vpn_active", lambda: None)
        monkeypatch.setattr(w, "start_runner", lambda: started.append(1) or True)
        w.show()
        w._autopick_done(None)
        assert shown and "белые списки" in shown[0]
        assert started and not w.busy
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
    finally:
        w.stop_watcher()
        _close(app, w)

def test_gui_games_table_updates_in_place(tmp_path, monkeypatch):
    """The table used to be rebuilt every 2 s: selection and scroll jumped, the page flickered."""
    monkeypatch.setattr(gamewatch.GameWatcher, "start", lambda self: None)
    app, w = _gui(tmp_path, monkeypatch, game_watch=True, games_schema=2,
                  games={"g.exe": {"path": "x", "ips": ["9.9.9.9"]}})
    try:
        w.start_watcher()
        item = w.table.item(0, 0)
        w.fill_games()
        assert w.table.item(0, 0) is item
        w.watcher._games["g.exe"]["ping_ms"] = 42
        w.fill_games()
        assert w.table.item(0, 2).text() == "42"
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

def test_gui_switched_off_game_filter_stays_off(tmp_path, monkeypatch):
    """Явно выключенный обход UDP для игр не включается обратно, когда игра найдена (issue #17)."""
    app, w = _gui(tmp_path, monkeypatch, game_watch=True)
    try:
        monkeypatch.setattr(w.tray, "showMessage", lambda *a: None)
        w.save(game_capture=True, game_tcp=True, game_udp=False)
        class _W:
            def games(self):
                return {"game.exe": {"path": "x", "ips": {"9.9.9.9"}, "active": True, "ping_ms": 50.0}}
        w.watcher = _W()
        assert w._game_flags() == (True, False)
        w.on_games_changed()
        assert w._game_flags() == (True, False)  # человек выключил, обратно не включаем
        w.save(game_tcp=False, game_udp=False)
        assert w._game_flags() == (False, False)
    finally:
        _close(app, w)

def test_gui_unchecked_game_filter_stays_off_after_auto_enable(tmp_path, monkeypatch):
    """Автовключение один раз, затем снятая галочка не возвращается и winws не перезапускается во время игры."""
    app, w = _gui(tmp_path, monkeypatch, game_watch=True)
    try:
        monkeypatch.setattr(w.tray, "showMessage", lambda *a: None)
        class _W:
            def games(self):
                return {"game.exe": {"path": "x", "ips": {"9.9.9.9"}, "active": True, "ping_ms": None}}
        w.watcher = _W()
        w.on_games_changed()  # первая игра: фильтр включился сам и запомнился
        assert w.settings.get("game_tcp") is True and w.settings.get("game_udp") is True
        w.save(game_tcp=False, game_udp=False)
        w._run_flags = (False, False)  # winws уже работает без игровых портов
        applied = []
        monkeypatch.setattr(w, "_apply_game_flags", lambda: applied.append(1))
        w.on_games_changed()  # игра найдена снова, но человек выключил
        assert w._game_flags() == (False, False) and applied == []
    finally:
        _close(app, w)

def test_gui_found_game_turns_the_filter_on_at_start(tmp_path, monkeypatch):
    """Игра уже идёт, галочки не трогали: игровой фильтр включается сразу, иначе вход в игру висит (2.5.1)."""
    app, w = _gui(tmp_path, monkeypatch, game_watch=True)
    try:
        monkeypatch.setattr(w.tray, "showMessage", lambda *a: None)
        class _W:
            def games(self):
                return {"game.exe": {"path": "x", "ips": {"9.9.9.9"}, "active": True, "ping_ms": None}}
        w.watcher = _W()
        assert "game_tcp" not in w.settings and "game_udp" not in w.settings
        assert w._game_flags() == (True, True)
        w.on_games_changed()  # запомнили: дальше решают галочки
        assert w.settings.get("game_udp") is True and w._game_flags() == (True, True)
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
