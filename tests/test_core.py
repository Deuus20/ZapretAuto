"""Offline unit tests: no winws, no network. Run: .venv\\Scripts\\python -m pytest -q"""
import importlib.util
import json
import re
import time
from pathlib import Path

import pytest

from app import gamewatch, paths, settings, strategies, tester, updater, vpn

ROOT = Path(__file__).resolve().parent.parent


# ---- strategies
def test_all_upstream_strategies_parse_and_resolve():
    all_s = strategies.load_all()
    assert len(all_s) >= 20
    for s in all_s:
        argv = s.argv(game_tcp=True, game_udp=True)
        assert argv[0].endswith("winws.exe")
        assert not any(re.search(r"%[\w~]+%", a) for a in argv), s.name
        assert argv.count("--new") == len(s.profiles) - 1


def test_game_and_vpn_lists_are_wired():
    s = strategies.load_all()[0]
    argv = s.argv()
    assert sum("ipset-exclude-vpn.txt" in a for a in argv) == len(s.profiles)
    assert any("ipset-games.txt" in a for a in argv)
    assert sum("ipset-exclude-games.txt" in a for a in argv) == sum("ipset-all" in p.group for p in s.profiles)


def test_mix_replaces_only_matching_group():
    by = {s.name: s for s in strategies.load_all()}
    base, other = by["ALT12"], by["FAKE TLS AUTO ALT"]
    g = "tcp:443:list-google"
    m = strategies.mix("mix", {g: other}, base)
    for p_mix, p_base in zip(m.profiles, base.profiles):
        if p_base.group == g:
            assert p_mix.args == next(q.args for q in other.profiles if q.group == g)
        else:
            assert p_mix.args == p_base.args


def test_broken_bat_is_skipped(tmp_path, monkeypatch):
    (tmp_path / "general (BROKEN).bat").write_text("@echo off\necho nothing\n", encoding="utf-8")
    src = next(paths.VENDOR.glob("general.bat"))
    (tmp_path / "general.bat").write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setattr(paths, "VENDOR", tmp_path)
    logs = []
    got = strategies.load_all(logs.append)
    assert [s.name for s in got] == ["default"] and logs


# ---- settings
def test_settings_atomic_and_corrupt_tolerant(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "DATA", tmp_path)
    settings._cache = None
    settings.save_settings(a=1)
    settings.save_settings(b=2)
    assert settings.load_settings() == {"a": 1, "b": 2}
    (tmp_path / "settings.json").write_text("{broken", encoding="utf-8")
    settings._cache = None
    assert settings.load_settings() == {}
    assert (tmp_path / "settings.json.bad").exists()


# ---- updater
@pytest.mark.parametrize("a,b,newer", [("1.10.0", "1.9.9d", True), ("1.9.9d", "1.9.9", True),
                                       ("1.10.3", "1.10.3", False), ("v1.0.1", "1.0.0", True)])
def test_is_newer(a, b, newer):
    assert updater.is_newer(a, b) is newer


def test_tag_and_host_validation():
    assert updater.TAG_RE.match("1.10.3") and not updater.TAG_RE.match("../../x")
    updater._check_url("https://github.com/x/y/releases/download/v1/a.zip")
    with pytest.raises(RuntimeError):
        updater._check_url("https://evil.example.com/a.zip")
    with pytest.raises(RuntimeError):
        updater._check_url("http://github.com/a.zip")


# ---- gamewatch
def test_classify_and_grouping():
    s = {"games_force": [r"D:\Games\Foo\Foo.exe"], "games_ignore": ["bad.exe"]}
    assert gamewatch.classify(r"D:\games\foo\FOO.exe", s)
    assert gamewatch.classify(r"C:\Steam\steamapps\common\X\x.exe", {})
    assert not gamewatch.classify(r"C:\Steam\steamapps\common\X\bad.exe", s)
    assert not gamewatch.classify(r"C:\Program Files\Google\Chrome\chrome.exe", {})
    ips = {f"5.5.5.{i}" for i in range(1, 5)}
    assert gamewatch._group(ips) == {"5.5.5.0/24"}
    assert gamewatch._group({"5.5.5.1", "6.6.6.6"}) == {"5.5.5.1", "6.6.6.6"}


def test_udp_heuristic_ignores_torrents():
    w = gamewatch.GameWatcher(lambda: None, log=lambda m: None)
    try:
        hits = [w._udp_game("game.exe", r"D:\x\game.exe", f"9.9.9.{i}", 27015, 17, {}) for i in range(3)]
        assert hits == [False, False, True]
        assert not any(w._udp_game("qbittorrent.exe", r"C:\q\qbittorrent.exe", f"9.9.8.{i}", 6881, 17, {})
                       for i in range(5))
    finally:
        w.stop()


# ---- vpn
def test_vpn_route_parsing(monkeypatch):
    rows = [("0.0.0.0", "0.0.0.0", "192.168.1.1", "192.168.1.70"),
            ("45.132.253.182", "255.255.255.255", "192.168.1.1", "192.168.1.70"),
            ("10.8.1.0", "255.255.255.0", "On-link", "10.8.1.3")]
    monkeypatch.setattr(vpn, "_routes", lambda: rows)
    monkeypatch.setattr(vpn, "_adapter_of", lambda ip: "Ethernet" if ip == "192.168.1.70" else "AmneziaVPN")
    monkeypatch.setattr(vpn.psutil, "net_connections", lambda kind: [])
    assert vpn.server_ips() == {"45.132.253.182"}
    assert vpn.bind_ip() == "192.168.1.70"
    assert vpn.VPN_RE.search("AmneziaVPN") and not vpn.VPN_RE.search("Laptop LAN")


# ---- shared DB merge script
def _merge_mod():
    spec = importlib.util.spec_from_file_location("merge_report", ROOT / "scripts" / "merge_report.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _body(**over):
    r = {"asn": "AS25513", "isp": "MGTS", "zapret": "1.10.3", "app": "1.0.8",
         "choice": {"base": "ALT12", "picks": {"tcp:443:list-google": "FAKE TLS AUTO ALT", "udp:other": "ALT3"}},
         "score": 23, "total": 28} | over
    return "text\n```json\n" + json.dumps(r) + "\n```\n"


def test_merge_votes_and_validation():
    m = _merge_mod()
    db = {}
    for _ in range(2):
        db = m.merge(db, m.parse(_body()))
    db = m.merge(db, m.parse(_body(choice={"base": "EXP", "picks": {}}, score=27)))
    e = db["AS25513"]["1.10.3"]
    assert e["choice"]["base"] == "ALT12" and e["votes"] == 2
    for bad in (_body(asn="AS1; rm"), _body(score=5), _body(choice={"base": "x$(y)", "picks": {}}), "no json"):
        with pytest.raises(ValueError):
            m.parse(bad)


# ---- logs / stats
def test_report_redacts_user_name():
    from app import logs
    assert logs._redact(r"C:\Users\Ivan\AppData\x") == r"C:\Users\<user>\AppData\x"
    assert logs._redact("c:/users/Ivan/x") == "c:/users/<user>/x"


def test_stats_uptime_and_checks(tmp_path, monkeypatch):
    from app import stats
    monkeypatch.setattr(paths, "DATA", tmp_path)
    t = [1000.0]
    monkeypatch.setattr(stats.time, "time", lambda: t[0] + 86400 * 100)
    stats.record("start")
    t[0] += 3600
    stats.record("check", ok=True)
    stats.record("check", ok=False)
    stats.record("stop")
    s = stats.summary(7)
    assert s["uptime_h"] == 1.0 and s["checks"] == 2 and s["ok_pct"] == 50


def test_settings_with_bom(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "DATA", tmp_path)
    settings._cache = None
    (tmp_path / "settings.json").write_text('{"a": 1}', encoding="utf-8-sig")
    assert settings.load_settings() == {"a": 1}


def test_launchers_and_web_ports_are_not_game_traffic():
    assert not gamewatch.classify(r"C:\Steam\steamapps\common\Wardogs\WardogsLauncher-Shipping.exe", {})
    assert gamewatch.classify(r"C:\Steam\steamapps\common\Wardogs\WardogsClient-Win64-Shipping.exe", {})
    assert {80, 443} <= gamewatch.SKIP_PORTS
    # the launcher's own web servers (Fastly, CloudFront) must not be desynced either
    assert gamewatch.web_client(r"C:\Steam\steamapps\common\Wardogs\WardogsLauncher-Shipping.exe", {})
    assert not gamewatch.web_client(r"C:\Program Files\Google\Chrome\Application\chrome.exe", {})


def test_merge_ignores_old_app_reports():
    m = _merge_mod()
    db = m.merge({}, m.parse(_body(app="1.0.3")))
    assert "choice" not in db["AS25513"]["1.10.3"]
    db = m.merge(db, m.parse(_body(app="1.0.8", choice={"base": "SIMPLE FAKE ALT", "picks": {}}, score=34, total=36)))
    assert db["AS25513"]["1.10.3"]["choice"]["base"] == "SIMPLE FAKE ALT"


def test_gamewatch_v1_ips_reset_once(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "DATA", tmp_path)
    monkeypatch.setattr(paths, "LISTS", tmp_path / "lists")
    settings._cache = None
    settings.save_settings(games={"g.exe": {"path": "x", "ips": ["1.2.3.4"]}})
    w = gamewatch.GameWatcher(lambda: None, log=lambda m: None)
    try:
        assert w.games()["g.exe"]["ips"] == set()
        assert settings.load_settings()["games_schema"] == 2
    finally:
        w.stop()


def test_gamewatch_ignores_web_ports_and_grace(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "DATA", tmp_path)
    monkeypatch.setattr(paths, "LISTS", tmp_path / "lists")
    settings._cache = None
    w = gamewatch.GameWatcher(lambda: None, log=lambda m: None)
    try:
        monkeypatch.setattr(w, "_proc", lambda pid: ("game.exe", r"C:\Steam\steamapps\common\G\game.exe"))
        monkeypatch.setattr(w, "_pool", type("P", (), {"submit": lambda *a, **k: None, "shutdown": lambda *a, **k: None})())
        w._process([(1, "9.9.9.9", 443, 6), (1, "9.9.9.10", 27015, 17)])
        g = w.games()["game.exe"]
        assert g["ips"] == {"9.9.9.10"}  # 443 skipped, game port kept
        assert w._web_nets == {"9.9.9.0/24"} | gamewatch.KNOWN_WEB_NETS  # its /24 is kept out of ipset-all profiles
        w._flush()
        assert "9.9.9.0/24" in (tmp_path / "lists" / "ipset-exclude-games.txt").read_text().split()
        assert "3.164.206.0/24" in settings.load_settings()["games_web"]  # Wardogs login: known before 1st launch
        assert time.time() - w._games["game.exe"]["started"] < gamewatch.LOGIN_GRACE
        w._games["game.exe"]["active"] = False
        w._process([(1, "9.9.9.11", 443, 6)])                     # login talks only 443: still "running"
        assert w.games()["game.exe"]["active"]
    finally:
        w.stop()


def test_diag_reports_missing_bypass_files(tmp_path, monkeypatch):
    from app import diag
    (tmp_path / "winws.exe").write_text("x")
    monkeypatch.setattr(paths, "BIN", tmp_path)
    assert set(diag.missing_files()) == {"WinDivert.dll", "WinDivert64.sys", "cygwin1.dll"}


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


def _fake_pick_env(monkeypatch, tmp_path, baseline_ok, vpn=None, bind=None):
    from app import tester, vpn as vpnmod
    monkeypatch.setattr(paths, "DATA", tmp_path)
    settings._cache = None
    targets = [tester.Target(f"T{i}", f"https://t{i}.example") for i in range(10)]
    monkeypatch.setattr(tester, "load_targets", lambda: targets)
    monkeypatch.setattr(tester, "probe_all",
                        lambda ts: {t.key: (0.1 if i < baseline_ok else None) for i, t in enumerate(ts)})
    monkeypatch.setattr(tester, "vpn_active", lambda: vpn)
    monkeypatch.setattr(vpnmod, "bind_ip", lambda: bind)
    monkeypatch.setattr(paths, "sync_lists", lambda: None)
    from app import diag
    monkeypatch.setattr(diag, "dns_poisoned", lambda: [])  # keep tests offline
    return tester


def test_auto_pick_keeps_strategy_when_nothing_is_blocked(tmp_path, monkeypatch):
    tester = _fake_pick_env(monkeypatch, tmp_path, baseline_ok=9)
    settings.save_settings(strategy={"base": "ALT12", "picks": {}})
    logs = []
    assert tester.auto_pick(logs.append, use_shared=False) == {"base": "ALT12", "picks": {}}
    assert any("почти ничего не блокируется" in m for m in logs)


def test_auto_pick_aborts_on_vpn_without_bypass_route(tmp_path, monkeypatch):
    tester = _fake_pick_env(monkeypatch, tmp_path, baseline_ok=3, vpn="AmneziaVPN", bind=None)
    logs = []
    assert tester.auto_pick(logs.append, use_shared=False) == {}
    assert any("VPN" in m for m in logs)


def test_quick_check_majority(tmp_path, monkeypatch):
    from app import tester
    monkeypatch.setattr(tester, "vpn_active", lambda: None)
    keys = ["DiscordMain", "DiscordGateway", "DiscordCDN", "YouTubeWeb", "YouTubeImage", "YouTubeVideoRedirect",
            "STUN_Google19302"]
    monkeypatch.setattr(tester, "load_targets", lambda: [tester.Target(k, "https://x") for k in keys])
    ok = {"DiscordMain", "DiscordGateway", "YouTubeImage", "STUN_Google19302"}
    monkeypatch.setattr(tester, "probe_all", lambda ts: {t.key: (0.1 if t.key in ok else None) for t in ts})
    monkeypatch.setattr(tester.time, "sleep", lambda s: None)
    r = tester.quick_check(light=True)
    assert r == {"Discord": True, "YouTube": False, "Голос Discord": True}


def test_auto_pick_keeps_current_unless_clearly_better(tmp_path, monkeypatch):
    tester = _fake_pick_env(monkeypatch, tmp_path, baseline_ok=3)
    settings.save_settings(strategy={"base": "ALT11", "picks": {}})
    keys = [f"T{i}" for i in range(10)]

    def fake_test(runner, s, targets, warmup=0):  # EXP passes 1 more probe than everyone else: noise level
        ok = 9 if s.name == "EXP" else 8
        return {k: (0.1 if i < ok else None) for i, k in enumerate(keys)}
    monkeypatch.setattr(tester, "test_strategy", fake_test)
    logs = []
    assert tester.auto_pick(logs.append, use_shared=False) == {"base": "ALT11", "picks": {}}
    assert any("оставляю текущую" in m for m in logs)
    lp = settings.load_settings()["last_pick"]
    assert lp["before"] == lp["score"] and lp["time"] > 0 and "services" in lp   # summary data for the GUI


def test_dns_poison_detection(monkeypatch):
    from app import diag
    import socket
    fake = {"discord.com": ["10.10.10.10"], "rutracker.org": ["1.2.3.4"], "www.youtube.com": ["142.251.150.4"],
            "youtube.com": None}

    def resolve(d):
        if fake[d] is None:  # ISP answers "no such domain" for a blocked site (since August 2026)
            raise socket.gaierror(11001, "host not found")
        return d, [], fake[d]
    monkeypatch.setattr(socket, "gethostbyname_ex", resolve)
    monkeypatch.setattr(socket, "gethostbyname", lambda d: "87.250.250.242")  # control domain resolves
    monkeypatch.setattr(diag, "_doh", lambda d: {"104.21.32.39"})
    assert diag.dns_poisoned() == ["discord.com", "rutracker.org", "youtube.com"]
    monkeypatch.setattr(socket, "gethostbyname", lambda d: (_ for _ in ()).throw(socket.gaierror(11001, "x")))
    assert diag.dns_poisoned() == ["discord.com", "rutracker.org"]  # offline: NXDOMAIN proves nothing
    monkeypatch.setattr(diag, "_doh", lambda d: (_ for _ in ()).throw(OSError("blocked")))
    assert diag.dns_poisoned() == ["discord.com"]  # DoH blocked: what is obviously forged still counts


def test_quic_targets_grouped_by_profile():
    pytest.importorskip("curl_cffi")
    from app import tester
    q = {t.key: t for t in tester.load_targets() if t.url.startswith("h3://")}
    assert q["YouTubeQUIC"].group.startswith("udp:443:")
    assert q["DiscordQUIC"].group == "udp:443:list-general"  # discord.com is in Flowseal's general list


def test_user_domain_add_remove(tmp_path, monkeypatch):
    from app import lists
    monkeypatch.setattr(paths, "LISTS", tmp_path)
    (tmp_path / "list-general-user.txt").write_text("# Never leave this file empty\ndomain.example.abc", encoding="utf-8")
    assert lists.add_user_domain("Example.org") is True
    assert lists.add_user_domain("example.org") is False  # case-insensitive duplicate
    text = (tmp_path / "list-general-user.txt").read_text(encoding="utf-8")
    assert text.splitlines()[-1] == "Example.org" and "abcExample" not in text  # added on its own line
    lists.remove_user_domain("EXAMPLE.org")
    assert "example" not in (tmp_path / "list-general-user.txt").read_text(encoding="utf-8").lower().replace(
        "domain.example.abc", "")


def test_vpn_exclude_file(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "LISTS", tmp_path)
    monkeypatch.setattr(vpn, "server_ips", lambda: {"45.132.253.182"})
    vpn.write_exclude()
    assert (tmp_path / "ipset-exclude-vpn.txt").read_text().split() == ["45.132.253.182"]
    monkeypatch.setattr(vpn, "server_ips", lambda: set())
    vpn.write_exclude()
    assert (tmp_path / "ipset-exclude-vpn.txt").read_text().split() == [vpn.PLACEHOLDER]  # never empty


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


def test_vpn_exclude_not_rewritten_when_unchanged(tmp_path, monkeypatch):
    import os
    monkeypatch.setattr(paths, "LISTS", tmp_path)
    monkeypatch.setattr(vpn, "server_ips", lambda: {"45.132.253.182"})
    vpn.write_exclude()
    f = tmp_path / "ipset-exclude-vpn.txt"
    os.utime(f, (1, 1))
    vpn.write_exclude()
    assert f.stat().st_mtime == 1  # untouched: winws would otherwise reload it every minute


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
    from app import updater
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


def test_known_game_seeded_on_first_sight(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "DATA", tmp_path)
    monkeypatch.setattr(paths, "LISTS", tmp_path / "lists")
    settings._cache = None
    w = gamewatch.GameWatcher(lambda: None, log=lambda m: None)
    try:
        exe = "wardogsclient-win64-shipping.exe"
        monkeypatch.setattr(w, "_proc", lambda pid: (exe, rf"C:\Steam\steamapps\common\Wardogs\{exe}"))
        monkeypatch.setattr(w, "_pool", type("P", (), {"submit": lambda *a, **k: None, "shutdown": lambda *a, **k: None})())
        w._process([(1, "9.9.9.10", 27015, 17)])
        assert len(w.games()[exe]["ips"]) > 100  # shipped servers + the new one
        assert "3.164.206.0/24" in w._web_nets
    finally:
        w.stop()


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
        assert not picks and not w.busy                 # still works: no 2–4 min re-pick
        res["YouTube"] = False
        w._update_done(True, True, True)
        assert picks == [1]                             # broken by the update: re-pick
    finally:
        w.tray.hide()
        w._timer.stop()
        w.deleteLater()
        app.processEvents()


def _games_mod():
    spec = importlib.util.spec_from_file_location("merge_games", ROOT / "scripts" / "merge_games.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_games_db_needs_two_reporters():
    m = _games_mod()
    body = lambda g, w=(): "```json\n" + json.dumps({"app": "1.2.0", "games": g, "web": list(w)}) + "\n```"
    db = m.merge({}, m.parse(body({"game.exe": ["9.9.9.9", "8.8.4.4"]}, ["3.164.206.0/24"])), "alice")
    assert db["games"] == {} and db["web"] == []                      # one person is not enough
    db = m.merge(db, m.parse(body({"game.exe": ["9.9.9.9"]})), "alice")
    assert db["games"] == {}                                          # the same person twice still is not
    db = m.merge(db, m.parse(body({"game.exe": ["9.9.9.9"]}, ["3.164.206.0/24"])), "Bob")
    assert db["games"] == {"game.exe": ["9.9.9.9"]} and db["web"] == ["3.164.206.0/24"]
    for bad in ({"game.exe": ["192.168.1.1"]}, {"../x.exe": ["9.9.9.9"]}, {"g.exe": "9.9.9.9"}):
        with pytest.raises(ValueError):
            m.parse(body(bad))
    with pytest.raises(ValueError):
        m.parse(body({}, ["10.0.0.0/24"]))


def test_games_report_url_fits():
    from app import sharedb
    ips = [f"9.{a}.{b}.1" for a in range(10) for b in range(100)]
    url = sharedb.games_report_url("1.2.0", {"big.exe": ips, "small.exe": ["9.9.9.9"]}, ["3.164.206.0/24"])
    assert len(url) <= sharedb.MAX_URL and "small.exe" in url and "%5Bgames%5D" in url


def test_shared_games_db_seeds_known_games(tmp_path, monkeypatch):
    from app import sharedb
    monkeypatch.setattr(paths, "DATA", tmp_path)
    monkeypatch.setattr(paths, "LISTS", tmp_path / "lists")
    settings._cache = None
    settings.save_settings(games={"old.exe": {"path": "x", "ips": ["7.7.7.7"]}}, games_schema=2)
    monkeypatch.setattr(sharedb, "games_db", lambda: {"games": {"old.exe": ["6.6.6.6", "10.0.0.1"],
                                                                "new.exe": ["5.5.5.5"]}, "web": ["4.4.4.0/24"]})
    monkeypatch.setattr(gamewatch, "KNOWN_WEB_HOSTS", ())
    w = gamewatch.GameWatcher(lambda: None, log=lambda m: None)
    try:
        w._resolve_known()
        assert w.games()["old.exe"]["ips"] == {"7.7.7.7", "6.6.6.6"}   # private IP from the base is dropped
        assert "4.4.4.0/24" in w._web_nets and "4.4.4.0/24" in settings.load_settings()["games_web"]
        assert w._seed("new.exe") == {"5.5.5.5"}
        monkeypatch.setattr(sharedb, "games_db", lambda: None)          # offline: last good copy is used
        assert gamewatch.GameWatcher._shared_db()["games"]["new.exe"] == ["5.5.5.5"]
    finally:
        w.stop()


def test_zapret2_argv_and_mode(tmp_path, monkeypatch):
    from app import zapret2
    monkeypatch.setattr(paths, "DATA", tmp_path)
    monkeypatch.setattr(paths, "LISTS", tmp_path / "lists")
    settings._cache = None
    a = zapret2.Strategy2().argv(True, False, True)
    assert a[0].endswith("winws2.exe") and "--wf-tcp-out=80,443,2053,2083,2087,2096,8443,1024-1934,1936-65535" in a
    assert "--wf-udp-out=443,12" in a                                   # UDP game filter off
    circ = [x for x in a if x.startswith("--lua-desync=") and ":strategy=" in x]
    nums = {int(x.rsplit("=", 1)[1]) for x in circ}
    assert nums == set(range(1, len(zapret2.ROTATION) + 1))             # circular: 1..N without gaps
    assert a.count("--lua-desync=circular:fails=3") == 3                # lists, discord.media, ipset-all
    assert any("ipset-exclude-games.txt" in x for x in a) and any("list-auto.txt" in x for x in a)
    settings.save_settings(zapret2=True, strategy={"base": "ALT8", "picks": {}})
    assert tester.chosen_strategy().name == "ALT8"                      # not downloaded yet: normal engine
    monkeypatch.setattr(zapret2, "installed", lambda: True)
    assert tester.chosen_strategy().name == zapret2.NAME


def test_zapret2_install_checks_hashes(tmp_path, monkeypatch):
    from app import zapret2
    monkeypatch.setattr(paths, "DATA", tmp_path)
    monkeypatch.setattr(zapret2, "FILES", {"a.txt": __import__("hashlib").sha256(b"good").hexdigest()})
    resp = type("R", (), {"content": b"evil"})
    monkeypatch.setattr(zapret2.requests, "get", lambda *a, **k: resp)
    assert not zapret2.install(lambda m: None) and not (tmp_path / "zapret2" / "a.txt").exists()
    resp.content = b"good"
    assert zapret2.install(lambda m: None) and zapret2.installed()


def _gui(tmp_path, monkeypatch, **st):
    pytest.importorskip("PySide6")
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setattr(paths, "DATA", tmp_path)
    monkeypatch.setattr(paths, "LISTS", tmp_path / "lists")
    settings._cache = None
    settings.save_settings(strategy={"base": "ALT8", "picks": {}}, ipset_updated=time.time(), **st)
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from app.gui.window import MainWindow
    return app, MainWindow()


def _close(app, w):
    w.tray.hide()
    w._timer.stop()
    w.deleteLater()
    app.processEvents()


def test_gui_zapret2_toggle(tmp_path, monkeypatch):
    from app import zapret2
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
        assert shown and "zapret2" in shown[-1] and not w.busy               # no pointless 2–4 min pick
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


def test_discord_cache_fix(tmp_path, monkeypatch):
    from app import diag
    monkeypatch.setenv("APPDATA", str(tmp_path))
    for d in ("discord/Cache", "discord/Code Cache", "discordptb/GPUCache", "discord/Local Storage"):
        (tmp_path / d).mkdir(parents=True)
    killed = []
    monkeypatch.setattr(diag, "_run", lambda c, timeout=20: killed.append(c[-1]) or (0, ""))
    monkeypatch.setattr("time.sleep", lambda s: None)
    assert len(diag._discord_caches()) == 3
    assert "очищен" in diag.apply_fix("discord_cache")
    assert not diag._discord_caches() and (tmp_path / "discord" / "Local Storage").exists()  # login kept
    assert "Discord.exe" in killed


def test_issue_report_is_redacted_and_fits(tmp_path, monkeypatch):
    from app import logs
    from urllib.parse import unquote
    monkeypatch.setattr(logs, "LOG_DIR", tmp_path)
    lines = [f"2026-10-20 01:00:{i:02d} INFO VPN сервер 185.1.2.{i % 250} C:/Users/Danil/x 192.168.1.5 " + "x" * 80
             for i in range(400)]
    (tmp_path / "zapretauto.log").write_text("\n".join(lines), encoding="utf-8")
    url = logs.issue_url("Discord не грузится", [{"label": "Подмена DNS", "level": "err", "detail": "youtube.com"},
                                                 {"label": "BFE", "level": "ok", "detail": "Running"}])
    text = unquote(url)
    assert len(url) <= logs.ISSUE_URL_MAX and "[log]" in text and "Discord не грузится" in text
    assert "Подмена DNS" in text and "BFE" not in text            # only problems from diagnostics
    assert "185.1.2." not in text and "<ip>" in text and "192.168.1.5" in text  # public IPs hidden, LAN kept
    assert "Danil" not in text and "01:00:" in text               # user name hidden; log tail included


def test_gui_chips_and_switch_rows(tmp_path, monkeypatch):
    from app.gui.widgets import SettingRow, StatusChip
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import QMouseEvent
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


def test_app_check_explains_hidden_project(monkeypatch):
    import requests
    import threading
    resp = requests.Response()
    resp.status_code = 404

    def gone():
        raise requests.HTTPError(response=resp)
    monkeypatch.setattr(updater, "app_release", gone)
    ev, got = threading.Event(), []
    updater.app_check_async(lambda t, n: (got.append((t, n)), ev.set()))
    assert ev.wait(5) and got == [(None, False)] and "недоступна" in updater.APP_CHECK_ERROR


def test_game_ports_custom_and_validated(tmp_path, monkeypatch):
    assert strategies.valid_ports("1024-1934,1936-65535") and strategies.valid_ports("27015")
    for bad in ("", "0-10", "100-50", "70000", "1024-", "a,b", "1024;2048"):
        assert not strategies.valid_ports(bad), bad
    app, w = _gui(tmp_path, monkeypatch)
    try:
        s = {x.name: x for x in strategies.load_all()}["ALT8"]
        assert any("1024-1934,1936-65535" in a for a in s.argv(True, True))   # default skips RTMP
        w.ports["tcp"].setText("2000-3000")
        w.on_ports("tcp")
        assert settings.load_settings()["game_ports_tcp"] == "2000-3000"
        assert any("2000-3000" in a for a in s.argv(True, True)) and not any("1936-65535" in a for a in s.argv(True))
        w.ports["udp"].setText("99999")
        w.on_ports("udp")                                                      # invalid: red, not saved
        assert w.ports["udp"].property("error") == "yes" and "game_ports_udp" not in settings.load_settings()
        w.ports["tcp"].setText("")
        w.on_ports("tcp")                                                      # empty: back to the default
        assert any("1024-1934,1936-65535" in a for a in s.argv(True))
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


def test_user_exclude_list_never_empty(tmp_path, monkeypatch):
    from app import lists
    monkeypatch.setattr(paths, "LISTS", tmp_path)
    assert lists.add_user_domain("bank.ru", "list-exclude-user.txt")
    assert (tmp_path / "list-exclude-user.txt").read_text(encoding="utf-8") == "bank.ru\n"
    lists.remove_user_domain("bank.ru", "list-exclude-user.txt")
    assert (tmp_path / "list-exclude-user.txt").read_text(encoding="utf-8") == "domain.example.abc\n"  # winws needs 1


def test_gui_site_broken_by_bypass_gets_excluded(tmp_path, monkeypatch):
    from app import lists
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


def test_video_cdn_probe(monkeypatch):
    import sys
    import types
    fake = types.SimpleNamespace(get=lambda url, **k: types.SimpleNamespace(
        text="109.1.2.3 => comstar-bka2 (109.252.16.0/21)\n"))
    monkeypatch.setitem(sys.modules, "curl_cffi", types.SimpleNamespace(requests=fake))
    monkeypatch.setitem(sys.modules, "curl_cffi.requests", fake)
    seen = []
    monkeypatch.setattr(tester, "fetch", lambda url, *a, **k: (seen.append(url), (204, url, 0))[1])
    monkeypatch.setattr(tester, "_CDN_NODE", None)
    assert tester.probe(tester.Target("YouTubeVideo", "ytcdn://")) is not None
    assert seen == ["https://rr1---comstar-bka2.googlevideo.com/generate_204"]
    assert "109.1.2.3" not in repr(vars(tester).get("_CDN_NODE"))      # the user's IP is not kept
    monkeypatch.setattr(tester, "fetch", lambda *a, **k: (_ for _ in ()).throw(OSError("reset")))
    assert tester.probe(tester.Target("YouTubeVideo", "ytcdn://")) is None and tester._CDN_NODE is None
