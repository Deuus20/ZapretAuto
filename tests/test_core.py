"""Offline unit tests: no winws, no network. Run: .venv\\Scripts\\python -m pytest -q"""
import importlib.util
import json
import re
import time
from pathlib import Path

import pytest

from app import gamewatch, paths, settings, strategies, updater, vpn

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
    assert gamewatch._group(ips, ips) == ips  # CDN-only IPs stay exact


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


def test_gamepick_majority_rating(monkeypatch):
    from app import gamepick
    seq = iter([True, False, True, True])
    monkeypatch.setattr(gamepick, "_attempt", lambda *a: (next(seq), 0, 0))
    assert gamepick._rate("g", "x", lambda: False, 3) == 2  # stops after 2 of 3 successes
    seq2 = iter([False, False, True])
    monkeypatch.setattr(gamepick, "_attempt", lambda *a: (next(seq2), 0, 0))
    assert gamepick._rate("g", "x", lambda: False, 3) == 0  # stops after 2 failures


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
        assert time.time() - w._games["game.exe"]["started"] < gamewatch.LOGIN_GRACE
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


def test_gamepick_saves_profile_not_global(tmp_path, monkeypatch):
    from app import gamepick
    monkeypatch.setattr(paths, "DATA", tmp_path)
    settings._cache = None
    settings.save_settings(strategy={"base": "ALT12", "picks": {}})
    monkeypatch.setattr(gamepick, "_attempt", lambda *a: (True, 9, 1.0))
    monkeypatch.setattr(gamepick, "_kill", lambda stem: None)
    monkeypatch.setattr(gamepick, "FlowCounter", lambda stem: (_ for _ in ()).throw(OSError()))
    monkeypatch.setattr(gamepick, "candidates", lambda: [{"base": "ALT11", "picks": {}}])
    monkeypatch.setattr(gamepick.tester, "quick_check", lambda: {"Discord": True, "YouTube": True})

    class R:
        def start(self, *a, **k): pass
        def stop(self): pass
    c = gamepick.pick_for_game(r"C:\G\game-Win64.exe", R(), log=lambda m: None, tries=1)
    st = settings.load_settings()
    assert c == {"base": "ALT11", "picks": {}}
    assert st["strategy"]["base"] == "ALT12"  # global untouched
    assert st["game_profiles"]["game-win64.exe"]["base"] == "ALT11"


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


def test_dns_poison_detection(monkeypatch):
    from app import diag
    import socket
    fake = {"discord.com": ["10.10.10.10"], "rutracker.org": ["1.2.3.4"], "www.youtube.com": ["142.251.150.4"]}
    monkeypatch.setattr(socket, "gethostbyname_ex", lambda d: (d, [], fake[d]))
    monkeypatch.setattr(diag, "_doh", lambda d: {"104.21.32.39"})
    assert diag.dns_poisoned() == ["discord.com", "rutracker.org"]  # private IP / no overlap with DoH
