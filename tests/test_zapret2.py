"""Экспериментальный режим zapret2."""

from app import paths, settings, tester, zapret2


def test_zapret2_argv_and_mode(tmp_path, monkeypatch):
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
    monkeypatch.setattr(paths, "DATA", tmp_path)
    monkeypatch.setattr(zapret2, "FILES", {"a.txt": __import__("hashlib").sha256(b"good").hexdigest()})
    resp = type("R", (), {"content": b"evil"})
    monkeypatch.setattr(zapret2.requests, "get", lambda *a, **k: resp)
    assert not zapret2.install(lambda m: None) and not (tmp_path / "zapret2" / "a.txt").exists()
    resp.content = b"good"
    assert zapret2.install(lambda m: None) and zapret2.installed()


def test_decide_when_to_switch_engines():
    now = 1_000_000.0
    # обычный движок не справился: пробуем zapret2 (не чаще раза в 6 часов)
    assert zapret2.decide(2, False, True, 0, now) == "install"
    assert zapret2.decide(1, False, True, 0, now) == "none"                      # одна неудача: рано
    assert zapret2.decide(2, False, False, 0, now) == "none"                     # автопереключение выключено
    assert zapret2.decide(2, False, True, now - 60, now) == "none"              # недавно уже переключали
    assert zapret2.decide(2, False, True, now - zapret2.SWITCH_PAUSE, now) == "install"
    # zapret2 не справился: обратно на обычный движок
    assert zapret2.decide(2, True, True, 0, now) == "revert"
    assert zapret2.decide(2, True, True, now - 60, now) == "none"
