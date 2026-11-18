"""Действия окна: обход, диагностика, подбор стратегии, отчёты."""
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QMessageBox,
    QPushButton,
    QTableWidgetItem,
    QWidget,
)

from app.gui.shared import (
    ROOT,
    TASK,
    UPDATE_EVERY,
    Worker,
    diag,
    dns,
    lists,
    logs,
    q,
    stats,
    strategies,
    tester,
    updater,
    whitelist,
)
from app.gui.sitecheck import _site_check, _with_direct_path

from .window_base import WindowBase

if TYPE_CHECKING:
    pass

class ActionsMixin(WindowBase):
    def notify(self, text: str):
        """Уведомление в трее; отключается галочкой «Уведомления»."""
        if self.settings.get("notifications", True):
            self.tray.showMessage("ZapretAuto", text)

    def run_diag(self):
        """Сначала обычные проверки, потом белые списки: их видно только без обхода, его выключаем на 5-10 с."""
        if self.busy:
            return
        self.btn_diag.setEnabled(False)
        self.btn_diag.setText("Проверка...")
        def first(rows):
            self._diag_rows = rows or []
            was = self.runner.running
            if was:
                self._expect_running = False
                self.runner.stop()
            def second(rep):
                if was:
                    self.start_runner()
                self.on_diag_done(list(self._diag_rows) + [whitelist.diag_item(rep)])
            def check(log):
                try:
                    return whitelist.check()
                except Exception as e:  # noqa: BLE001 (не даём одной проверке уронить диагностику)
                    log(f"белые списки: не удалось проверить ({e})")
                    return {"verdict": "unknown"}
            self._run(check, second)
        self._run(lambda log: diag.run_checks(), first)

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
            detail = r["detail"] or ""
            cell = QTableWidgetItem(detail if len(detail) <= 180 else detail[:177].rstrip() + "…")
            cell.setToolTip(detail)  # полный текст: в таблице он бы растянул строку на три
            t.setItem(i, 1, cell)
            if r.get("fix"):
                b = q(QPushButton(r.get("fix_label") or "Исправить"), objectName="tonal" if r["level"] != "ok" else "text")
                b.setCursor(Qt.CursorShape.PointingHandCursor)
                b.clicked.connect(lambda _=False, f=r["fix"]: self.apply_fix(f))
                cell = QWidget()  # centered, natural height: a bare cell widget stretched over a tall row
                cl = QHBoxLayout(cell)
                cl.setContentsMargins(4, 2, 4, 2)
                cl.addWidget(b, 0, Qt.AlignmentFlag.AlignVCenter)
                t.setCellWidget(i, 2, cell)
        t.resizeRowsToContents()

    def apply_fix(self, fix):
        """Fixes may hit the network or kill processes: run them off the UI thread, then show the result."""
        self.btn_diag.setEnabled(False)
        def done(msg):
            msg = msg or "не удалось, подробности в журнале"
            self.log(f"исправление {fix}: {msg}")
            QMessageBox.information(self, "Диагностика", msg)
            self.run_diag()
        self._run(lambda log: diag.apply_fix(fix), done)

    def _update_list_count(self):
        n = sum(1 for ln in self.list_edit.toPlainText().splitlines()
                if ln.strip() and not ln.strip().startswith("#"))
        self.lbl_list_count.setText(f"Записей: {n}")

    def load_list(self, *_):
        if self.list_combo.currentData() == "apps_exclude":
            self.list_edit.setPlainText("".join(x + "\n" for x in self.settings.get("apps_exclude", [])))
            self._update_list_count()
            return
        p = lists.user_list_paths()[self.list_combo.currentData()]
        try:
            self.list_edit.setPlainText(p.read_text(encoding="utf-8"))
        except OSError:
            self.list_edit.setPlainText("")
        self._update_list_count()

    def save_list(self):
        if self.list_combo.currentData() == "apps_exclude":
            apps = sorted({ln.strip().lower() for ln in self.list_edit.toPlainText().splitlines()
                           if ln.strip().lower().endswith(".exe")})
            self.save(apps_exclude=apps)
            self._update_list_count()
            self.log("сохранён список программ, которые обход не трогает")
            return
        p = lists.user_list_paths()[self.list_combo.currentData()]
        p.parent.mkdir(parents=True, exist_ok=True)
        text = self.list_edit.toPlainText()
        if not any(ln.strip() and not ln.strip().startswith("#") for ln in text.splitlines()):
            # winws refuses empty lists (and re-reads them live): keep a harmless placeholder entry
            text = text.rstrip() + ("\n" if text.strip() else "") + (
                "203.0.113.113/32" if p.name.startswith("ipset") else "domain.example.abc") + "\n"
            self.list_edit.setPlainText(text)
        from app import paths
        paths.write_list(p, text)
        self._update_list_count()
        self.log(f"сохранён {p.name}")

    def update_ipset(self):
        self.btn_ipset.setEnabled(False)
        self._run(lambda log: lists.update_ipset(log), lambda ok: self.btn_ipset.setEnabled(True))

    def start_runner(self):
        if self.busy:
            return False
        s = tester.chosen_strategy()
        if not s:
            self.error = "Стратегия не выбрана, нажмите «Подобрать лучшую стратегию»."
            self.refresh()
            return False
        try:
            flags = self._game_flags()
            skip = {ip for ips in self.settings.get("nocapture", {}).values() for ip in ips}
            self.runner.start(s, *flags, self.settings.get("auto_hostlist", True), skip)
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
        def work(log):
            rows = diag_rows
            if rows is None:  # diagnostics not run yet: the report is much less useful without them
                log("отчёт: запускаю диагностику…")
                rows = diag.run_checks()
            return logs.make_report(Path(path), rows)
        self._run(work, done)

    def pause(self, minutes: int):
        """Turn the bypass off for a while (e.g. a site that dislikes it) and back on automatically."""
        if self.busy or not self.runner.running:
            return
        self._expect_running = False
        self.runner.stop()
        stats.record("stop")
        self._resume_at = time.time() + minutes * 60
        self.log(f"обход выключен на {minutes} мин")
        self.notify(f"Обход выключен на {minutes} минут, потом включится сам.")
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
            return _with_direct_path(lambda: _site_check(dom, self.runner.running))
        def done(msg):
            if msg:
                if msg.endswith(" [ip]"):  # IP-blocked: hand the domain over for the VPN's site list
                    msg = msg[:-5]
                    QApplication.clipboard().setText(dom)
                    msg += (f" Адрес «{dom}» скопирован, вставьте его в список сайтов раздельного "
                            "туннелирования вашего VPN.")
                self.log(msg)
                QMessageBox.information(self, "Проверка сайта", msg)
        self.log(f"проверяю {dom}…")
        self._run(work, done)

    def _health(self):
        """Background check of key services; two failed checks in a row -> re-pick automatically.
        Once an hour a short YouTube sample is added: the site may open while video is throttled."""
        if self._health_running or self.busy or not self.runner.running:
            return
        speed = time.time() - getattr(self, "_last_speed_check", 0) > 3600
        if speed:
            self._last_speed_check = time.time()
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
                self._telegram_hint(res)
                if not getattr(self, "_net_remembered", False) and not self._zapret2_on():
                    self._net_remembered = True  # works here: remember it for this ISP (once per network)
                    c = self.settings.get("strategy")
                    if c:
                        self._run(lambda log: tester.remember_network(c), lambda _: None)
                return
            self._health_fails += 1
            bad = ", ".join(k for k, v in res.items() if not v)
            self.log(f"проверка: не работает {bad} ({self._health_fails}/2)")
            self._next_health = time.time() + 60  # re-check soon
            if self._health_fails >= 2 and self._zapret2_on():
                self._health_fails = 0  # a pick would test winws strategies: not what runs now
                self._zapret2_revert(bad)
                return
            if self._health_fails >= 2 and self.settings.get("auto_repick", True):
                self._health_fails = 0
                if self._game_running():
                    if not self._repick_pending:
                        self._repick_pending = True
                        self.notify(f"Перестало работать: {bad}. "
                                              "Починю после игры, чтобы не прерывать матч.")
                    return
                if (time.time() - self.settings.get("last_auto_repick", 0) < 6 * 3600
                        and not getattr(self, "_new_net", False)):
                    self.log("автоподбор уже был недавно, жду, чтобы не дёргать обход")
                    return
                self.save(last_auto_repick=time.time())
                self._new_net = False
                stats.record("repick", reason=bad)
                self.notify(f"Перестало работать: {bad}. Подбираю стратегию заново…")
                self._repair = True
                self.autopick()
        self._run(lambda log: tester.quick_check(light=True, speed=speed), done)  # no 1 MB test every 10 min

    def _telegram_hint(self, res: dict):
        """Telegram gets its own answer: the bypass beats DPI on its sites, but not a block by IP address."""
        app, web = res.get("Telegram (приложение)"), res.get("Telegram (веб)")
        if app is not False:
            self._tg_advised = False
            return
        if getattr(self, "_tg_advised", False):
            return
        self._tg_advised = True
        if web is False:
            self.log("Telegram: не отвечают ни сайт, ни серверы. Похоже на блокировку по IP: нужен прокси или VPN.")
        else:
            self.log("Telegram: сайт открывается, а приложение не подключается к серверам. Похоже на блокировку "
                     "по IP: стратегия обхода тут не поможет, нужен прокси или VPN.")

    def _zapret2_revert(self, bad: str):
        """zapret2 не справился: вернуться к обычному движку и сохранённой стратегии этой сети."""
        from app import zapret2
        if zapret2.decide(2, True, self.settings.get("zapret2_auto", True),
                          self.settings.get("last_zapret2_switch", 0)) != "revert":
            self.log(f"режим zapret2: не работает {bad}, но движок меняю не чаще раза в 6 часов")
            return
        if not self.settings.get("strategy"):
            self.log(f"zapret2: не работает {bad}, а вернуть не на что: нажмите «Подобрать заново»")
            return
        self.save(zapret2=False, last_zapret2_switch=time.time())
        self.cb["zapret2"].blockSignals(True)
        self.cb["zapret2"].setChecked(False)
        self.cb["zapret2"].blockSignals(False)
        self.log(f"zapret2 не справился ({bad}), возвращаюсь к обычному обходу")
        self.notify("zapret2 не справился, вернул обычный обход.")
        self.start_runner()
        self.refresh()

    def _zapret2_offer(self) -> bool:
        """Подбор не нашёл рабочую стратегию: другой движок может. В первый раз спрашиваем, дальше решаем сами."""
        from app import zapret2
        if not self.settings.get("zapret2_auto", True) or self._zapret2_on():
            return False
        if zapret2.decide(2, False, True, self.settings.get("last_zapret2_switch", 0)) != "install":
            self.log("движок не меняю: недавно уже переключал (не чаще раза в 6 часов)")
            return False
        if not self.settings.get("zapret2_asked"):
            self.save(zapret2_asked=True)
            box = QMessageBox(self)
            box.setWindowTitle("Не помогло автоматически")
            box.setText("Подбор не нашёл рабочую стратегию. Попробовать движок zapret2?\n\n"
                        "Он подбирает стратегию для каждого сайта сам. При первом включении скачается около 4 МБ, "
                        "файлы проверяются по контрольным суммам.")
            yes = box.addButton("Попробовать zapret2", QMessageBox.ButtonRole.AcceptRole)
            box.addButton("Остаться на обычном обходе", QMessageBox.ButtonRole.RejectRole)
            box.exec()
            if box.clickedButton() is not yes:
                self.save(zapret2_auto=False)
                self.log("zapret2 сам включать не буду, можно включить вручную в «Дополнительно»")
                return False
        self.save(last_zapret2_switch=time.time())
        self._expect_running = True
        self.set_zapret2(True)
        return True

    def _network_changed(self):
        """Another ISP (ASN): switch to the strategy picked there before, without a pick. An unknown ISP may be
        re-picked at once if checks fail (the 6 h pause between automatic picks is for one network)."""
        if tester.vpn_active():
            return
        from app import sharedb
        def done(who):
            if not who or who[0] == self.settings.get("net_asn"):
                return
            self.save(net_asn=who[0])
            self._net_remembered = False
            self.fill_isp()
            mem = (self.settings.get("networks") or {}).get(who[0])
            if not mem:
                self._new_net = True
                self.log(f"новая сеть: {who[1]} ({who[0]}), если что-то не откроется, подберу стратегию для неё")
                return
            self._new_net = False
            if mem["choice"] == self.settings.get("strategy") or self.busy or self._zapret2_on():
                return
            self.save(strategy=mem["choice"], auto_choice=mem["choice"])
            self.log(f"сеть {who[1]}: включаю стратегию, подобранную для неё раньше")
            if self.runner.running:
                self.start_runner()
            self.refresh()
        self._run(lambda log: sharedb.isp(), done)

    def _vpn_off(self):
        """VPN switched off: Discord stays stuck on connections made through it until restarted."""
        if not self.settings.get("discord_after_vpn", True) or not self.runner.running:
            return
        from app import vpn
        def done(names):
            if names:
                self.log("VPN выключен, перезапустил Discord, чтобы он подключился через обход")
        self._run(lambda log: vpn.restart_discord(), done)

    def _tick(self):
        """Detect winws dying on its own (antivirus, driver conflict)."""
        now = time.time()
        self._ticks = getattr(self, "_ticks", 0) + 1
        resume = getattr(self, "_resume_at", 0)
        if resume and time.time() >= resume:
            self._resume_at = 0
            if not self.runner.running and not self.busy:
                self.start_runner()
                self.notify("Обход снова включён.")
        if (updater and self.settings.get("auto_update", True) and not self.busy
                and now >= getattr(self, "_next_update_check", now + 1) and not self._game_running()
                and not self._update_deferred(now)):
            self._next_update_check = now + UPDATE_EVERY  # the app runs for weeks: don't update only at start
            self._auto_check = True
            self.check_update()
        if self._ticks % 5 == 0:
            self.apply_theme()  # Windows light/dark switched: follow it live
            from app import runner as runner_mod
            if self.runner.running and not self.busy and runner_mod.stale(self.runner.unwatched):
                self.log("изменился список исключений, перезапускаю обход, чтобы он его учёл")
                self.start_runner()
        if self._ticks % 30 == 0 and self.runner.running and not self.busy:  # ~1 min: VPN may have moved
            from app import vpn
            def vpn_moved(ips):  # the list reloads by itself, the capture filter only on restart
                if ips is not None and ips != self.runner.vpn_ips and self.runner.running and not self.busy:
                    self.log("сменился сервер VPN, перезапускаю обход, чтобы не пропускать туннель через winws")
                    self.start_runner()
            self._run(lambda log: vpn.write_exclude(), vpn_moved)
        if now - self._last_tick > 60:  # timer is 2 s: a long gap means sleep/hibernate
            self.log("пробуждение после сна, перезапускаю обход")
            if self._expect_running and not self.busy:
                self.start_runner()
            self._next_health = now + 30
        self._last_tick = now
        if self._expect_running and not self.busy and not self.runner.running:
            stats.record("crash")
            self._crashes = [t for t in self._crashes if now - t < 600] + [now]
            if len(self._crashes) <= 3:
                from app import runner as runner_mod
                why = runner_mod.explain(runner_mod.last_output())
                self.log(f"winws завершился сам, перезапуск ({len(self._crashes)}/3)" + (f": {why}" if why else ""))
                self.start_runner()
            else:
                self._expect_running = False
                self.error = "Обход постоянно падает. Проверьте антивирус и раздел «Диагностика»."
                self.log("winws падает слишком часто, автоперезапуск остановлен")
                self.notify(self.error)
                self._explain_failure()
        if self._repick_pending and not self.busy and not self._game_running():
            self._repick_pending = False
            self.log("игра закрыта, чиню обход")
            self.autopick()
        net = tester.egress_ip() if int(now) % 10 < 2 else self._net  # ~every 10 s; psutil-free (it leaked handles)
        if self._net is not None and net != self._net:
            self.log(f"сеть изменилась ({self._net} → {net}), проверю доступ")
            self._next_health = now + 20
            if net:
                self._network_changed()
        if net and net != self._net:
            vpn_now = tester.vpn_active()
            if self._vpn_was and not vpn_now:
                self._vpn_off()
            self._vpn_was = vpn_now
        self._net = net
        # read the file, not self.settings: update_ipset saves the time from a worker thread, and the window's
        # copy kept the old one, so the "weekly" download ran every hour
        if (not self.busy and now - getattr(self, "_ipset_try", 0) > 3600
                and now - tester.load_settings().get("ipset_updated", 0) > 7 * 86400):
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
        """A worker finished: join its thread (frees the OS thread handle) and drop the QObject, the signal
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
                self.substatus.setText("VPN не пускает трафик мимо себя, проверка невозможна, пока он включён"
                                       if tester.vpn_active() else "Не удалось проверить")
                return
            txt = "   ".join(f"{k} {'✓' if ok else '✗'}" for k, ok in res.items())
            self.set_chips(res)
            self.log("проверка доступа: " + txt)
            self.substatus.setText("Всё работает" if all(res.values()) else
                                   "Если что-то ✗, нажмите «Подобрать лучшую стратегию»")
            if notify:  # asked from the tray menu: answer there too
                self.notify(txt)
        self._run(lambda log: tester.quick_check(), done)

    def _dns_servers(self, mode: str) -> list[str]:
        """Адреса для режима карточки DNS; пустой список значит «нечего ставить»."""
        if mode == "custom":
            text = self.dns_edit.text().replace(";", ",")
            return [x.strip() for x in text.split(",") if dns.valid_ip(x)]
        for name, ips, _doh in dns.RESOLVERS:
            if name == mode:
                return list(ips)
        return []

    def dns_apply(self):
        """Проверить и поставить DNS: авто-подбор лучшего, популярный из списка или свой."""
        if self.busy:
            return
        mode = self.dns_combo.currentData()
        if mode == "provider":
            self.dns_restore()
            return
        servers = self._dns_servers(mode)
        if mode == "custom" and not servers:
            QMessageBox.warning(self, "DNS", "Укажите IPv4-адреса через запятую, например 1.1.1.1, 1.0.0.1")
            return
        self.btn_dns.setEnabled(False)
        self.btn_dns.setText("Проверяю…")
        name = self.dns_combo.currentText()  # читаем в GUI-потоке: из рабочего трогать виджеты нельзя
        def work(log):
            if mode == "auto":
                b = dns.best(log)
                if not b:
                    return False, ("Не нашёл рабочий DNS: похоже, провайдер не пускает внешние DNS "
                                   "(белые списки). Оставил как было."), [], ""
                if b["name"].startswith("Провайдер"):  # уже стоит: не превращаем DHCP в статику без нужды
                    return True, "DNS провайдера отвечает верно и быстрее всех: менять не нужно", [], \
                        "провайдер (лучший)"
                ok, msg = dns.apply(list(b["ips"]))
                return ok, (f"{b['name']}: {msg}" if ok else msg), (list(b["ips"]) if ok else []), \
                    f"{b['name']} (авто)"
            ok, msg = dns.apply(servers)
            return ok, msg, (servers if ok else []), name
        def done(res):
            ok, msg, cur, name = res or (False, "не удалось проверить DNS", [], "")
            self.btn_dns.setEnabled(True)
            self.btn_dns.setText("Проверить и применить")
            self.log("DNS: " + msg)
            if ok:
                self.save(dns_servers=cur, dns_name=name, dns_checked=time.time())
                self.fill_dns()
            QMessageBox.information(self, "DNS", msg)
        self._run(work, done)

    def dns_restore(self):
        """Вернуть DNS от провайдера (DHCP)."""
        if self.busy:
            return
        def done(res):
            ok, msg = res or (False, "не удалось вернуть DNS провайдера")
            self.log("DNS: " + msg)
            if ok:
                self.save(dns_servers=[], dns_name="", dns_mode="provider", dns_checked=time.time())
                i = self.dns_combo.findData("provider")
                if i >= 0 and self.dns_combo.currentIndex() != i:
                    self.dns_combo.setCurrentIndex(i)  # сохранит dns_mode через dns_mode_changed
                self.fill_dns()
            QMessageBox.information(self, "DNS", msg)
        self._run(lambda log: dns.restore(), done)

    def _dns_blocked_help(self):
        """Подбор не начинался из-за подмены DNS: починить DNS или подобрать всё равно."""
        box = QMessageBox(self)
        box.setWindowTitle("Подмена DNS")
        box.setText("Провайдер подменяет DNS, подбор стратегии отменён: на таких ответах он выбрал бы случайную "
                    "стратегию.")
        box.setInformativeText("Можно починить DNS (программа подберёт рабочий сервер для этой сети, включит DoH "
                               "в браузерах и запишет адреса в hosts) или подобрать без этого.")
        fix = box.addButton("Починить DNS и подобрать", QMessageBox.ButtonRole.AcceptRole)
        raw = box.addButton("Подобрать без DNS", QMessageBox.ButtonRole.RejectRole)
        box.addButton("Позже", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton() is fix:
            self._dns_repair_then_pick()
        elif box.clickedButton() is raw:
            self._pick_ignore_dns()

    def _dns_repair_then_pick(self):
        """Починить DNS и вернуться к подбору; если не вышло, предложить подбор без DNS."""
        self.busy = True
        self.busy_text = "Чиню DNS…"
        self.refresh()
        def work(log):
            msg = dns.repair(log)
            return msg, not diag.dns_poisoned(), dns.current()
        def done(res):
            self.busy = False
            msg, ok, cur = res or ("не удалось починить DNS", False, [])
            self.log("DNS: " + msg)
            if ok:
                self.save(dns_servers=cur, dns_name="починка DNS", dns_checked=time.time())
                self.fill_dns()
            self.refresh()
            if self.isVisible():
                QMessageBox.information(self, "DNS", msg)
            if ok:
                self.autopick()
            elif self.isVisible() and QMessageBox.question(
                    self, "DNS", "Починить DNS не удалось. Подобрать всё равно? Результат может быть случайным.") \
                    == QMessageBox.StandardButton.Yes:
                self._pick_ignore_dns()
        self._run(work, done)

    def _pick_ignore_dns(self):
        """Подобрать, не трогая DNS: пользователь предупреждён, результат может быть случайным."""
        self._pick_ignore = True
        try:
            self.autopick()
        finally:
            self._pick_ignore = False

    def _game_udp_wanted(self) -> bool:
        """A game was found: enable the game filter for TCP and UDP (some games log in over TCP high ports).
        Games the user keeps out of the bypass do not turn the game filter on."""
        excl = {x.lower() for x in self.settings.get("games_exclude", [])}
        return bool(self.watcher and any(g.get("ips") and exe not in excl
                                        for exe, g in self.watcher.games().items()))

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
        elif key == "game_capture":
            if self.watcher:
                self.watcher.set_capture(v)
            if self.runner.running and not self.busy:
                self.start_runner()  # the game ports leave/enter the capture filter only on restart
            self.fill_games()
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
                self.error = "Не удалось скачать zapret2, подробности в журнале."
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

    @staticmethod
    def autostart_command() -> list[str]:
        """PowerShell that (re)creates the logon task. Not schtasks /Create: its defaults kill the task after 72 h
        (the app runs for weeks), don't start it on battery and stop it when a laptop is unplugged."""
        import os
        def q(s) -> str:
            return "'" + str(s).replace("'", "''") + "'"
        if getattr(sys, "frozen", False):
            exe, args = sys.executable, "--tray"
        else:
            exe, args = sys.executable, f'"{ROOT / "main.py"}" --tray'
        user = f"{os.environ.get('USERDOMAIN', '')}\\{os.environ.get('USERNAME', '')}".lstrip("\\")
        ps = (f"$a = New-ScheduledTaskAction -Execute {q(exe)} -Argument {q(args)}; "
              f"$t = New-ScheduledTaskTrigger -AtLogOn -User {q(user)}; "
              "$s = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries "
              "-ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew; "
              f"$p = New-ScheduledTaskPrincipal -UserId {q(user)} -LogonType Interactive -RunLevel Highest; "
              f"Register-ScheduledTask -TaskName {q(TASK)} -Action $a -Trigger $t -Settings $s -Principal $p "
              "-Force | Out-Null")
        return ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps]

    def set_autostart(self, on: bool):
        cmd = self.autostart_command() if on else ["schtasks", "/Delete", "/F", "/TN", TASK]
        r = subprocess.run(cmd, capture_output=True, text=True, creationflags=0x08000000)
        self.log(f"автозапуск {'вкл' if on else 'выкл'}: " + ("ок" if r.returncode == 0 else (r.stderr or r.stdout).strip()))

    def ask_autopick(self):
        self.show_window()
        if QMessageBox.question(
                self, "Первая настройка",
                "Нужно один раз подобрать стратегию под вашего провайдера.\n\n"
                "Это займёт 2-4 минуты. В это время Discord и YouTube могут ненадолго "
                "пропадать, это нормально. VPN на время подбора лучше выключить.\n\n"
                "Начать?") == QMessageBox.StandardButton.Yes:
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
            self.log(f"VPN {vpn} включён, подбор пойдёт мимо него")
        self._expect_running = False
        self.runner.stop()
        self.busy = True
        self.busy_text = "Подбираю стратегию…"
        self.error = ""
        self.refresh()
        self.substatus.setText("Обычно 2-4 минуты. Обход на это время выключен.")
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
        ignore = bool(getattr(self, "_pick_ignore", False) or self.settings.get("pick_ignore_dns", False))
        self._run(lambda log: tester.auto_pick(log, stop=lambda: self._cancel, ignore_dns=ignore),
                  self._autopick_done, line)

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
        if not res and not cancelled and tester.blocked_reason() == "dns":
            self.error = ""
            self._repair = False
            if self.settings.get("strategy"):  # autopick stopped the bypass: the pick never ran, bring it back
                self.start_runner()
            if self.isVisible():
                self._dns_blocked_help()
            else:  # never pop a dialog from an automatic repair behind the user's back
                self.notify("Подбор отменён: провайдер подменяет DNS. "
                                                    "Почините DNS в «Дополнительно», иначе стратегия случайна.")
            self.refresh()
            return
        if not res and not cancelled:
            self.error = "Подбор не удался, подробности в разделе «Журнал»."
        if getattr(self, "_repair", False):  # this pick was an automatic repair: tell how it ended
            self._repair = False
            self.notify(f"Обход починен: {self.describe()}" if res else
                                  "Починить автоматически не удалось, откройте окно программы")
        if res:
            self.start_runner()
            self._pick_summary()
            self._offer_autostart()
            self._offer_share()
            self.fill_isp()
        elif cancelled:
            if self.settings.get("strategy"):
                self.start_runner()
        else:
            self._no_pick_help()  # why did nothing work: allowlist network? another engine? then the report offer
        self.refresh()

    def _no_pick_help(self):
        """Nothing worked: check for an allowlist network before offering another engine or a report."""
        if time.time() - self.settings.get("white_checked", 0) < 6 * 3600 or tester.vpn_active():
            self._no_pick_fallback()  # checked recently (or VPN hides the real picture): don't probe again
            return
        self.log("подбор не помог, проверяю, не белые ли списки у провайдера…")
        def done(rep):
            self.save(white_checked=time.time())
            if (rep or {}).get("verdict") == "whitelist":
                self.log(whitelist.TEXT)
                self.notify(whitelist.TEXT)
                if self.isVisible():
                    QMessageBox.warning(self, "Белые списки", whitelist.TEXT)
                if self.settings.get("strategy"):
                    self.start_runner()
                return
            self._no_pick_fallback()
        self._run(lambda log: whitelist.check(), done)

    def _no_pick_fallback(self):
        """Another engine may cope where no winws strategy did; otherwise offer a report."""
        if self._zapret2_offer():
            return  # set_zapret2 restarts the bypass itself
        if self.settings.get("strategy"):
            self.start_runner()
        self._offer_failed_report()

    def _offer_failed_report(self):
        if self.isVisible() and QMessageBox.question(
                self, "ZapretAuto", "Подбор не помог. Отправить отчёт разработчику?\n\nОткроется страница GitHub "
                "с готовым отчётом (версии и конец журнала, имя пользователя и IP скрыты). Нужен аккаунт GitHub.") \
                == QMessageBox.StandardButton.Yes:
            self.send_report()

    def _explain_failure(self):
        """Run diagnostics in the background and put the concrete cause into the status line."""
        def done(cause):
            if cause:
                self.error = "Обход не работает. " + cause
                self.log("причина: " + cause)
                self.notify(self.error)
                self.refresh()
        from app import runner as runner_mod
        out = runner_mod.last_output()
        if out:
            self.log("вывод обхода:\n" + out)  # winws' own words go to the log/report
        cause = runner_mod.explain(out)
        if cause:  # winws said why: no need to guess from diagnostics
            done(cause)
            return
        self._run(lambda log: diag.first_problem(), done)

    def _offer_autostart(self):
        """After the first successful setup: most people want the bypass to come up with Windows."""
        if self.settings.get("autostart") or self.settings.get("autostart_asked"):
            return
        self.save(autostart_asked=True)
        if QMessageBox.question(self, "ZapretAuto", "Готово! Запускать ZapretAuto вместе с Windows?\n\n"
                                "Программа будет тихо стартовать в трее, и Discord/YouTube заработают сразу "
                                "после включения компьютера.") == QMessageBox.StandardButton.Yes:
            self.cb["autostart"].setChecked(True)  # saves the setting and creates the task

    def _offer_share(self):
        lp = self.settings.get("last_pick", {})
        if lp.get("shared") or self.settings.get("share_never") or not lp.get("total"):
            return
        box = QMessageBox(self)
        box.setWindowTitle("Поделиться результатом")
        box.setText("Подбор завершён. Отправить результат в общую базу?\n\n"
                    "Другие пользователи вашего провайдера смогут настроиться за секунды. "
                    "Отправляется только номер сети провайдера (AS) и найденная комбинация, "
                    "без вашего IP. Откроется страница GitHub (нужен аккаунт).")
        yes = box.addButton("Поделиться", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("Не сейчас", QMessageBox.ButtonRole.RejectRole)
        never = box.addButton("Больше не спрашивать", QMessageBox.ButtonRole.DestructiveRole)
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
