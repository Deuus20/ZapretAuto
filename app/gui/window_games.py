"""Страница игр: слежение, список, исключения, общая база адресов."""
import time
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QFileDialog, QMessageBox

from app.gui.shared import gamewatch

from .window_base import WindowBase

if TYPE_CHECKING:
    pass

class GamesMixin(WindowBase):
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
        if not self.settings.get("game_capture", True):
            return (False, False)  # games are left alone: no game ports in the capture filter
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
                if time.time() - getattr(self, "_games_logged", 0) < 60:
                    return  # the list is written within a second of each new server: one line a minute is enough
                msg = "обновлены адреса игровых серверов"
            self._games_logged = time.time()
            self.log(msg)

    def _selected_game(self) -> str | None:
        r = self.table.currentRow()
        item = self.table.item(r, 0) if r >= 0 else None
        if item is None:
            QMessageBox.information(self, "ZapretAuto", "Сначала выберите игру в списке.")
            return None
        return item.data(Qt.ItemDataRole.UserRole)

    def ignore_game(self):
        exe = self._selected_game()
        if not exe:
            return
        self.set_game_ignored(exe, True)

    def set_game_ignored(self, exe: str, on: bool):
        ign = set(self.settings.get("games_ignore", []))
        excl = {x.lower() for x in self.settings.get("games_exclude", [])}
        nocap = dict(self.settings.get("nocapture", {}))  # exe -> its servers, kept out of winws entirely
        if on:
            g = (self.watcher.games().get(exe) if self.watcher else None) or self.settings.get("games", {}).get(exe)
            if g and g.get("ips"):
                nocap[exe] = sorted(g["ips"])
        else:
            nocap.pop(exe, None)
        self.save(games_ignore=sorted(ign | {exe} if on else ign - {exe}), nocapture=nocap,
                  games_exclude=sorted(excl - {exe}))  # an ignored program is not a game: no exclusion needed
        if self.runner.running and not self.busy:
            self.start_runner()  # the capture filter changes only on restart
        if on and self.watcher:  # not a game: its servers must leave the game ipset (and the game filter)
            self.watcher.set_excluded(exe, False)
            self.watcher.reset_game(exe)
        self.log(f"{exe}: обход для игры " + ("выключен" if on else "снова включён"))
        self.fill_games()

    def _on_game_check(self, item):
        """«Обход» column: a checked box keeps the game's servers in the bypass."""
        if item.column() != 4:
            return
        name = self.table.item(item.row(), 0)
        exe = name.data(Qt.ItemDataRole.UserRole) if name else None
        if exe:
            self.set_game_excluded(exe, item.checkState() != Qt.CheckState.Checked)

    def set_game_excluded(self, exe: str, on: bool):
        """Keep one game out of the bypass without removing it from the list (the «Обход» checkbox)."""
        if not self.settings.get("game_capture", True) and on:
            return  # the global switch is off: nothing to bring back per game
        excl = {x.lower() for x in self.settings.get("games_exclude", [])}
        nocap = dict(self.settings.get("nocapture", {}))
        if on:
            g = (self.watcher.games().get(exe) if self.watcher else None) or self.settings.get("games", {}).get(exe)
            if g and g.get("ips"):
                nocap[exe] = sorted(g["ips"])
        else:
            nocap.pop(exe, None)
        self.save(games_exclude=sorted(excl | {exe} if on else excl - {exe}), nocapture=nocap)
        if self.watcher:
            self.watcher.set_excluded(exe, on)  # ipset-exclude-games: winws re-reads it live
        if self.runner.running and not self.busy:
            self.start_runner()  # the capture filter changes only on restart
        self.log(f"{exe}: " + ("вне обхода — его серверы обход не трогает" if on else "снова в обходе"))
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
        self._ensure_watcher()
        self.log(f"обход не будет трогать серверы {exe}")
        QMessageBox.information(self, "ZapretAuto", f"Готово: обход перестанет трогать серверы {exe}.\n\n"
                                "Перезапустите программу — её адреса соберутся сами за пару секунд, и она "
                                "должна заработать. Отменить: «Списки» → «Программы, которые обход не трогает».")

    def _ensure_watcher(self):
        if not self.watcher:
            self.cb["game_watch"].blockSignals(True)
            self.cb["game_watch"].setChecked(True)
            self.cb["game_watch"].blockSignals(False)
            self.start_watcher()

    def add_game(self):
        path, _ = QFileDialog.getOpenFileName(self, "Выберите exe игры", "", "Программы (*.exe)")
        if path:
            exe = path.replace("/", "\\")
            name = exe.rsplit("\\", 1)[-1].lower()
            ignored = name in {x.lower() for x in self.settings.get("games_ignore", [])}
            if self.watcher and name in self.watcher.games() and not ignored:
                rows = [(it.data(Qt.ItemDataRole.UserRole) if it is not None else None)
                        for it in (self.table.item(i, 0) for i in range(self.table.rowCount()))]
                if name in rows:
                    self.table.selectRow(rows.index(name))
                QMessageBox.information(self, "ZapretAuto", f"{name} уже есть в списке игр.")
                return
            self.save(games_force=sorted(set(self.settings.get("games_force", [])) | {exe}),
                      games_ignore=[x for x in self.settings.get("games_ignore", []) if x.lower() != name],
                      games_exclude=[x for x in self.settings.get("games_exclude", []) if x.lower() != name],
                      game_watch=True)  # adding a game by hand means "watch it": also undo «Не игра»/«вне обхода»
            self._ensure_watcher()
            if self.watcher:
                self.watcher.set_excluded(name, False)
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
        if QMessageBox.question(self, "Поделиться адресами игр", text) != QMessageBox.StandardButton.Yes:
            return
        import webbrowser

        from app import __version__, sharedb
        webbrowser.open(sharedb.games_report_url(__version__, found,
                                                 self.watcher.web_nets() if self.watcher else []))
        self.log("открыта страница отправки адресов игр")
