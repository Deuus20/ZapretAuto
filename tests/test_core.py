"""Offline unit tests: no winws, no network. Run: .venv\\Scripts\\python -m pytest -q"""
import importlib.util
import json
import re
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
    r = {"asn": "AS25513", "isp": "MGTS", "zapret": "1.10.3", "app": "1.0.3",
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
