"""Общие базы стратегий и игровых адресов."""
import json

import pytest
from helpers import _body, _games_mod, _merge_mod

from app import gamewatch, logs, paths, settings, sharedb


def test_merge_votes_and_validation():
    m = _merge_mod()
    db = m.merge({}, m.parse(_body()), "alice")
    assert "choice" not in db["AS25513"]["1.10.3"]          # одного голоса для выбора провайдера мало
    db = m.merge(db, m.parse(_body()), "alice")             # тот же автор второй голос не даёт
    assert db["AS25513"]["1.10.3"]["votes"] == 1 and "choice" not in db["AS25513"]["1.10.3"]
    db = m.merge(db, m.parse(_body()), "Bob")
    e = db["AS25513"]["1.10.3"]
    assert e["choice"]["base"] == "ALT12" and e["votes"] == 2
    for bad in (_body(asn="AS1; rm"), _body(score=5), _body(choice={"base": "x$(y)", "picks": {}}), "no json"):
        with pytest.raises(ValueError):
            m.parse(bad)

def test_one_author_cannot_fill_the_history():
    m = _merge_mod()
    db = {}
    for i in range(12):  # много issue от одного аккаунта не должны накручивать голоса
        db = m.merge(db, m.parse(_body(choice={"base": f"STRAT {i}", "picks": {}})), "spammer")
    e = db["AS25513"]["1.10.3"]
    assert len(e["reports"]) <= m.MAX_PER_AUTHOR and "choice" not in e and e["votes"] <= 1

def test_legacy_reports_without_author_count_once():
    """В старой базе у отчётов нет автора: это не повод считать их разными людьми."""
    m = _merge_mod()
    db = {"AS1": {"1.10.3": {"isp": "X", "reports": [
        {"choice": {"base": "ALT12", "picks": {}}, "score": 20, "total": 25, "app": "1.0.8"},
        {"choice": {"base": "ALT12", "picks": {}}, "score": 21, "total": 25, "app": "1.0.8"}]}}}
    merged = m.merge(db, m.parse(_body(asn="AS1")))
    assert merged["AS1"]["1.10.3"]["votes"] == 1 and "choice" not in merged["AS1"]["1.10.3"]

def test_report_redacts_user_name():
    assert logs._redact(r"C:\Users\Ivan\AppData\x") == r"C:\Users\<user>\AppData\x"
    assert logs._redact("c:/users/Ivan/x") == "c:/users/<user>/x"
    assert logs._redact(r"D:\Users\Ivan\x") == r"D:\Users\<user>\x"   # профиль не только на C:


def test_public_report_hides_ipv6_and_other_drives():
    text = "connect 2a00:1450:4001:81b::200e and 8.8.8.8 and 192.168.1.5 and D:\\Users\\Danil\\x"
    out = logs._redact_public(text)
    assert "2a00:1450" not in out and "8.8.8.8" not in out and "<ip>" in out
    assert "192.168.1.5" in out          # адрес своей сети не скрываем: он нужен для разбора
    assert "Danil" not in out and "D:\\Users\\<user>" in out


def test_public_report_hides_ip_with_port():
    """`1.2.3.4:443` раньше проходил целиком: токен с портом не разбирался как адрес."""
    for text, want in (("connect 1.2.3.4:443", "connect <ip>:443"),
                       ("https://8.8.8.8:443/x", "https://<ip>:443/x"),
                       ("udp 77.88.8.8:53", "udp <ip>:53")):
        assert logs._redact_public(text) == want
    assert logs._redact_public("192.168.1.5:8080") == "192.168.1.5:8080"  # своя сеть остаётся


def test_merge_report_handles_the_real_db():
    """Настоящий strategies-db.json из репозитория должен приниматься и переживать слияние без потерь."""
    m = _merge_mod()
    db = json.loads((settings.paths.ROOT / "strategies-db.json").read_text(encoding="utf-8"))
    assert isinstance(db, dict) and db, "база стратегий не должна быть пустой"
    for asn, per_version in db.items():
        assert asn.startswith("AS"), asn
        for version, entry in per_version.items():
            assert isinstance(entry.get("reports", []), list), (asn, version)
    merged = m.merge(json.loads(json.dumps(db)), m.parse(_body(asn="AS64500")), "alice")
    merged = m.merge(merged, m.parse(_body(asn="AS64500")), "bob")
    assert merged["AS64500"]["1.10.3"]["choice"]["base"] == "ALT12"
    assert "AS64500" not in db  # исходная база не меняется: слияние возвращает новое дерево

def test_merge_ignores_old_app_reports():
    m = _merge_mod()
    db = m.merge({}, m.parse(_body(app="1.0.3")), "alice")
    assert "choice" not in db["AS25513"]["1.10.3"]
    modern = _body(app="1.0.8", choice={"base": "SIMPLE FAKE ALT", "picks": {}}, score=34, total=36)
    db = m.merge(db, m.parse(modern), "alice")
    db = m.merge(db, m.parse(modern), "bob")
    assert db["AS25513"]["1.10.3"]["choice"]["base"] == "SIMPLE FAKE ALT"

def test_games_db_needs_two_reporters():
    m = _games_mod()
    def body(g, w=()):
        return "```json\n" + json.dumps({"app": "1.2.0", "games": g, "web": list(w)}) + "\n```"
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
    ips = [f"9.{a}.{b}.1" for a in range(10) for b in range(100)]
    url = sharedb.games_report_url("1.2.0", {"big.exe": ips, "small.exe": ["9.9.9.9"]}, ["3.164.206.0/24"])
    assert len(url) <= sharedb.MAX_URL and "small.exe" in url and "%5Bgames%5D" in url

def test_issue_report_is_redacted_and_fits(tmp_path, monkeypatch):
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

def test_shared_games_db_retried_when_offline_at_start(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "DATA", tmp_path)
    monkeypatch.setattr(paths, "LISTS", tmp_path / "lists")
    settings._cache = None
    settings.save_settings(games={}, games_schema=2)
    answers = [None, None, {"games": {}, "web": ["4.4.4.0/24"]}]
    monkeypatch.setattr(sharedb, "games_db", lambda: answers.pop(0))
    monkeypatch.setattr(gamewatch, "KNOWN_WEB_HOSTS", ())
    w = gamewatch.GameWatcher(lambda: None, log=lambda m: None)
    waits = []
    monkeypatch.setattr(w._stop_ev, "wait", lambda t: waits.append(t) or False)
    try:
        w._resolve_known()
        assert waits == [120, 240] and "4.4.4.0/24" in w._web_nets and not answers
    finally:
        w.stop()
