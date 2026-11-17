"""Слежение за играми: классификация процессов, адреса серверов."""
import time

from helpers import _close, _gui

from app import gamewatch, paths, settings, sharedb, strategies


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

def test_launchers_and_web_ports_are_not_game_traffic():
    assert not gamewatch.classify(r"C:\Steam\steamapps\common\Wardogs\WardogsLauncher-Shipping.exe", {})
    assert gamewatch.classify(r"C:\Steam\steamapps\common\Wardogs\WardogsClient-Win64-Shipping.exe", {})
    assert {80, 443} <= gamewatch.SKIP_PORTS
    # the launcher's own web servers (Fastly, CloudFront) must not be desynced either
    assert gamewatch.web_client(r"C:\Steam\steamapps\common\Wardogs\WardogsLauncher-Shipping.exe", {})
    assert not gamewatch.web_client(r"C:\Program Files\Google\Chrome\Application\chrome.exe", {})

def test_dns_resolvers_never_learned():
    """1.1.1.1 and 8.8.8.8 are resolvers, not game servers: desyncing them breaks DNS for the whole PC."""
    w = gamewatch.GameWatcher(lambda: None, log=lambda m: None)
    try:
        g = {"ips": set(), "seen": {}}
        assert not w._add_ip(g, "1.1.1.1", time.time())
        assert not w._add_ip(g, "8.8.8.8", time.time())
        assert w._add_ip(g, "5.6.7.8", time.time())
        assert g["ips"] == {"5.6.7.8"}
    finally:
        w.stop()

def test_render_drops_stale_dns_entries(tmp_path, monkeypatch):
    """Lists saved by older versions kept 1.1.1.1: they must leave on the next flush."""
    monkeypatch.setattr(paths, "DATA", tmp_path)
    monkeypatch.setattr(paths, "LISTS", tmp_path / "lists")
    settings._cache = None
    w = gamewatch.GameWatcher(lambda: None, log=lambda m: None)
    try:
        w._games["g.exe"] = {"path": "x", "ips": {"1.1.1.1", "5.6.7.8"}, "seen": {}}
        w._capture = True
        text = w._render()
        assert "1.1.1.1" not in text and "5.6.7.8" in text
    finally:
        w.stop()

def test_gamewatch_v1_ips_reset_once(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "DATA", tmp_path)
    monkeypatch.setattr(paths, "LISTS", tmp_path / "lists")
    settings._cache = None
    settings.save_settings(games={"g.exe": {"path": "x", "ips": ["1.2.3.4"]}})
    w = gamewatch.GameWatcher(lambda: None, log=lambda m: None)
    try:
        assert w.games()["g.exe"]["ips"] == set()
        assert settings.load_settings()["games_schema"] == 3
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
        excl = tmp_path / "lists" / "ipset-exclude-games.txt"
        assert excl.read_text().split() == [paths.USER_LISTS["ipset-exclude-games.txt"].strip()]  # off by default
        settings.save_settings(games_web_bypass=False)
        w._pending_since, w._last_web = None, None
        w._flush()
        assert "9.9.9.0/24" in excl.read_text().split()
        assert "3.164.206.0/24" in settings.load_settings()["games_web"]  # Wardogs login: known before 1st launch
        assert time.time() - w._games["game.exe"]["started"] < gamewatch.LOGIN_GRACE
        w._games["game.exe"]["active"] = False
        w._process([(1, "9.9.9.11", 443, 6)])                     # login talks only 443: still "running"
        assert w.games()["game.exe"]["active"]
    finally:
        w.stop()

def test_gamewatch_capture_off_keeps_servers_out_of_bypass(tmp_path, monkeypatch):
    """«Перехватывать подключения игр» off: servers are still collected, but only to keep them out of the bypass."""
    monkeypatch.setattr(paths, "DATA", tmp_path)
    monkeypatch.setattr(paths, "LISTS", tmp_path / "lists")
    settings._cache = None
    settings.save_settings(game_capture=False, games_schema=3,
                           games={"g.exe": {"path": "x", "ips": ["5.6.7.8"]}})
    w = gamewatch.GameWatcher(lambda: None, log=lambda m: None)
    try:
        assert "5.6.7.8" not in w._render()
        w._flush()
        excl = (tmp_path / "lists" / "ipset-exclude-games.txt").read_text(encoding="utf-8").split()
        assert "5.6.7.8" in excl  # ipset-all ranges must not desync it either
        assert w.games()["g.exe"]["excluded"]
    finally:
        w.stop()

def test_game_excluded_by_hand_leaves_only_that_game_out(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "DATA", tmp_path)
    monkeypatch.setattr(paths, "LISTS", tmp_path / "lists")
    settings._cache = None
    settings.save_settings(games_schema=3, games_exclude=["a.exe"],
                           games={"a.exe": {"path": "x", "ips": ["5.6.7.8"]},
                                  "b.exe": {"path": "y", "ips": ["6.7.8.9"]}})
    w = gamewatch.GameWatcher(lambda: None, log=lambda m: None)
    try:
        text, excl = w._render(), None
        assert "5.6.7.8" not in text and "6.7.8.9" in text
        w._flush()
        excl = (tmp_path / "lists" / "ipset-exclude-games.txt").read_text(encoding="utf-8").split()
        assert "5.6.7.8" in excl and "6.7.8.9" not in excl
        assert w.games()["a.exe"]["excluded"] and not w.games()["b.exe"]["excluded"]
        w.set_excluded("a.exe", False)  # back into the bypass: the list is rewritten at once
        assert "5.6.7.8" in w._render()
    finally:
        w.stop()

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
        ips = w.games()[exe]["ips"]
        assert "9.9.9.10" in ips and "54.115.9.225" in ips and not any(gamewatch.valve_sdr(i) for i in ips)
        assert "155.133.224.0/19" in w._render()  # shipped Valve relays: their whole nets
        assert "3.164.206.0/24" in w._web_nets
    finally:
        w.stop()

def test_game_servers_rotate_cdn_never_learned(tmp_path, monkeypatch):
    """Servers unseen for a week leave; a full list drops the oldest; Cloudflare (login) is never desynced."""
    monkeypatch.setattr(paths, "DATA", tmp_path)
    monkeypatch.setattr(paths, "LISTS", tmp_path / "lists")
    settings._cache = None
    exe = "game.exe"
    settings.save_settings(games_schema=3, games={exe: {"path": "x", "ips": ["9.9.9.1", "9.9.9.2", "104.21.5.5"],
                                                        "seen": {"9.9.9.1": 1000}}})
    w = gamewatch.GameWatcher(lambda: None, log=lambda m: None)
    try:
        g = w._games[exe]
        assert g["ips"] == {"9.9.9.2"}  # 9.9.9.1 unseen since 1970-ish: expired; Cloudflare dropped
        now = time.time()
        assert not w._add_ip(g, "104.18.125.108", now) and "104.18.125.108" not in g["ips"]
        monkeypatch.setattr(gamewatch, "MAX_IPS_PER_GAME", 3)
        g["seen"]["9.9.9.2"] = now - 100
        for ip in ("8.8.8.1", "8.8.8.2"):
            w._add_ip(g, ip, now)
        assert w._add_ip(g, "8.8.8.3", now) and g["ips"] == {"8.8.8.1", "8.8.8.2", "8.8.8.3"}  # oldest went
        assert w._expire(now + gamewatch.IP_TTL + 1) and not g["ips"]
    finally:
        w.stop()

def test_shared_games_db_seeds_known_games(tmp_path, monkeypatch):
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

def test_store_apps_are_not_games(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "DATA", tmp_path)
    monkeypatch.setattr(paths, "LISTS", tmp_path / "lists")
    settings._cache = None
    store = r"C:\Program Files\WindowsApps\Claude_2.4_x64__pzs8\app\claude.exe"
    assert not gamewatch.classify(store, {})
    settings.save_settings(games_schema=2, games={"claude.exe": {"path": store, "ips": ["9.9.9.9"]},
                                                   "game.exe": {"path": r"D:\Games\game.exe", "ips": []}})
    w = gamewatch.GameWatcher(lambda: None, log=lambda m: None)
    try:
        assert set(w.games()) == {"game.exe"} and set(settings.load_settings()["games"]) == {"game.exe"}
    finally:
        w._pool.shutdown()

def test_known_sdr_game_gets_valve_nets_after_reset(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "DATA", tmp_path)
    monkeypatch.setattr(paths, "LISTS", tmp_path / "lists")
    settings._cache = None
    settings.save_settings(games_schema=3, games={"wardogsclient-win64-shipping.exe": {"path": "x", "ips": []}})
    w = gamewatch.GameWatcher(lambda: None, log=lambda m: None)
    try:
        assert "155.133.224.0/19" in w._render()
    finally:
        w.stop()

def test_process_scan_is_throttled(monkeypatch):
    calls = []
    monkeypatch.setattr(gamewatch.psutil, "process_iter", lambda attrs: calls.append(1) or [])
    w = gamewatch.GameWatcher(lambda: None, log=lambda m: None)
    for _ in range(10):  # a running game with no new flows asks every 0.5 s tick
        w._running_names()
    assert len(calls) == 1

def test_game_client_idling_in_menu_is_not_playing(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "DATA", tmp_path)
    monkeypatch.setattr(paths, "LISTS", tmp_path / "lists")
    settings._cache = None
    settings.save_settings(games={}, games_schema=2)
    w = gamewatch.GameWatcher(lambda: None, log=lambda m: None)
    try:
        now = time.time()
        for exe in ("robloxplayerbeta.exe", "riot client.exe"):
            w._games[exe] = {"path": exe, "ips": set(), "active": True, "ping_ms": None, "started": now - 600}
        names = {"robloxplayerbeta.exe", "riot client.exe"}
        monkeypatch.setattr(w, "_running_names", lambda: names)
        monkeypatch.setattr(w, "_proc", lambda pid: {1: ("robloxplayerbeta.exe", "x")}.get(pid, (None, None)))
        w._handle = object()                                   # flow capture on
        w._open_flows = {77: 1}                                # in a match: an open UDP flow
        w._process([])
        g = w.games()
        assert g["robloxplayerbeta.exe"]["active"] and not g["riot client.exe"]["active"]   # client: never
        w._open_flows = {}                                     # back in the menu: flows closed
        w._process([])
        assert not w.games()["robloxplayerbeta.exe"]["active"]
    finally:
        w._handle = None
        w.stop()

def test_udp_game_heuristic_needs_a_burst(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "DATA", tmp_path)
    w = gamewatch.GameWatcher(lambda: None, log=lambda m: None)
    try:
        t = [1000.0]
        monkeypatch.setattr(gamewatch.time, "time", lambda: t[0])
        for ip in ("5.5.5.1", "5.5.5.2"):
            assert not w._udp_game("sync.exe", r"c:\apps\sync.exe", ip, 40000, 17, {})
            t[0] += 86400                                     # one peer a day: never a game
        assert not w._udp_game("sync.exe", r"c:\apps\sync.exe", "5.5.5.3", 40000, 17, {})
        for ip in ("6.6.6.1", "6.6.6.2", "6.6.6.3"):
            r = w._udp_game("game.exe", r"c:\g\game.exe", ip, 40000, 17, {})
        assert r                                              # a burst is
    finally:
        w.stop()
