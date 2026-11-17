"""Обновления программы и zapret: проверка, установка, проверка после установки."""
import time
from typing import TYPE_CHECKING

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QMessageBox

from app.gui.shared import tester, updater

from .window_base import WindowBase

if TYPE_CHECKING:
    pass

class UpdatesMixin(WindowBase):
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

    def _update_deferred(self, now: float) -> bool:
        """«Обновляться только в тишине»: put the update off while a call or a full-screen app runs, re-checking
        every 10 min; after a day of waiting it goes anyway (old versions break as upstream moves on)."""
        if not self.settings.get("quiet_updates"):
            return False
        since = getattr(self, "_deferred_since", 0) or now
        from app import quiet
        why = quiet.busy_reason() if now - since < 86400 else None
        if not why:
            self._deferred_since = 0
            return False
        if not getattr(self, "_deferred_since", 0):
            self.log(f"обновление отложено: {why}")
        self._deferred_since = since
        self._next_update_check = now + 600
        return True

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
        if auto and (self.busy or self._game_running()):  # a game/pick began during the check: not now
            self._next_update_check = time.time() + 600
            return
        if auto or QMessageBox.question(
                self, "ZapretAuto", f"Установить ZapretAuto {tag}?") == QMessageBox.StandardButton.Yes:
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
            if newer and self._game_running():  # a game started during the check: swap zapret later
                self._next_update_check = time.time() + 600
            elif newer:
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
