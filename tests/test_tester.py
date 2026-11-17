"""Подбор стратегии, замеры, ipset."""
import time

import pytest
from helpers import _fake_pick_env

from app import gamewatch, paths, settings, tester


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
    monkeypatch.setattr(tester, "egress_ip", lambda: "1.2.3.4")
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
    assert settings.load_settings()["networks"]["AS100"]["choice"] == {"base": "ALT11", "picks": {}}

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

def test_game_ip_burst_does_not_delay_the_ipset(tmp_path, monkeypatch):
    """New servers in a login burst: the first one sets the write deadline, the rest don't push it back."""
    monkeypatch.setattr(paths, "DATA", tmp_path)
    monkeypatch.setattr(paths, "LISTS", tmp_path / "lists")
    settings._cache = None
    w = gamewatch.GameWatcher(lambda: None, log=lambda m: None)
    try:
        exe = "game.exe"
        monkeypatch.setattr(w, "_proc", lambda pid: (exe, rf"D:\steamapps\common\G\{exe}"))
        monkeypatch.setattr(w, "_pool", type("P", (), {"submit": lambda *a, **k: None, "shutdown": lambda *a, **k: None})())
        w._process([(1, "9.9.9.10", 27015, 17)])
        first = w._pending_since
        w._process([(1, "9.9.9.11", 27015, 17)])
        assert first and w._pending_since == first
        assert gamewatch.DEBOUNCE + gamewatch.FAST_INTERVAL <= 1.0
    finally:
        w.stop()

def test_targets_fallback_and_bind_reset(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "VENDOR", tmp_path)                      # upstream dropped utils/targets.txt
    assert {"DiscordMain", "YouTubeWeb"} <= {t.key for t in tester.load_targets()}
    def boom(*a):
        tester.BIND = "192.168.1.5"
        raise RuntimeError("pick failed")
    monkeypatch.setattr(tester, "_auto_pick", boom)
    with pytest.raises(RuntimeError):
        tester.auto_pick()
    assert tester.BIND is None

def test_fetch_freeze_after_16k_is_a_failure(monkeypatch):
    import sys
    import types
    class Sess:
        def __init__(self, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def get(self, url, content_callback, **k):
            content_callback(b"x" * 16384)                     # DPI lets the first 16 KB through...
            raise TimeoutError("operation timed out")          # ...then the session freezes
    fake = types.ModuleType("curl_cffi")
    fake.requests = types.SimpleNamespace(Session=Sess)
    monkeypatch.setitem(sys.modules, "curl_cffi", fake)
    with pytest.raises(TimeoutError):
        tester.fetch("https://x", tester.READ_BYTES, 5)
    assert tester.probe(tester.Target("YouTubeWeb", "https://x")) is None

def test_ipset_hosts_are_scored_for_the_ipset_profile(tmp_path, monkeypatch):
    import socket
    monkeypatch.setattr(paths, "LISTS", tmp_path)
    monkeypatch.setattr(paths, "DATA", tmp_path)
    settings._cache = None
    (tmp_path / "ipset-all.txt").write_text("151.101.0.0/16\n", encoding="utf-8")
    monkeypatch.setattr(tester, "_domains", lambda name: set())
    ips = {"fastly.example": "151.101.1.1", "other.example": "5.5.5.5"}
    monkeypatch.setattr(socket, "gethostbyname", lambda h: ips[h])
    assert tester.target_group(tester.Target("F", "https://fastly.example")) == "tcp:80,443,8443:ipset-all"
    assert tester.target_group(tester.Target("O", "https://other.example")) == "tcp:80,443:list-general"

def test_ipset_ranges_are_merged_and_searched_by_bisect(tmp_path, monkeypatch):
    import socket
    monkeypatch.setattr(paths, "LISTS", tmp_path)
    (tmp_path / "ipset-all.txt").write_text("10.0.0.0/24\n10.0.0.128/25\n192.168.0.0/16\n203.0.113.7\n",
                                            encoding="utf-8")
    ips = {"a": "10.0.0.200", "b": "192.168.5.5", "c": "203.0.113.7", "d": "8.8.8.8"}
    monkeypatch.setattr(socket, "gethostbyname", lambda h: ips[h])
    assert tester._in_ipset_all("a") and tester._in_ipset_all("b") and tester._in_ipset_all("c")
    assert tester._in_ipset_all("d") is False
    starts, ends = tester._ipset_cache[1], tester._ipset_cache[2]
    assert len(starts) == 3 and starts == sorted(starts)  # 10.0.0.0/24 + 10.0.0.128/25 слились в один
    assert (starts[0], ends[0]) == (int(__import__("ipaddress").IPv4Address("10.0.0.0")),
                                   int(__import__("ipaddress").IPv4Address("10.0.0.255")))

def test_pick_restores_auto_hostlist(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "LISTS", tmp_path)
    (tmp_path / "list-auto.txt").write_text("good.example\n", encoding="utf-8")
    def pick(*a):
        (tmp_path / "list-auto.txt").write_text("good.example\nbroken-by-candidate.example\n", encoding="utf-8")
        return {}
    monkeypatch.setattr(tester, "_auto_pick", pick)
    tester.auto_pick()
    assert (tmp_path / "list-auto.txt").read_text(encoding="utf-8") == "good.example\n"

def test_rolled_back_servers_are_banned_for_a_while():
    now = time.time()
    s = {"games_bad_ips": ["1.1.1.1", "2.2.2.2", "3.3.3.3"],
         "games_bad_time": {"1.1.1.1": now - 3600, "2.2.2.2": now - 10 * 86400}}
    assert gamewatch.active_bans(s) == {"1.1.1.1"}         # old and untimed bans expire
