"""Разбор general*.bat, mix, списки."""
import re

from app import paths, strategies


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

def test_steam_client_not_desynced(tmp_path, monkeypatch):
    """SDR games put Valve's nets into ipset-games; Steam's own TCP link (CM 155.133.x:27019) sits there too and
    desync broke it (Shift+Tab overlay, friends). Valve nets leave every TCP ipset profile, UDP keeps them."""
    for s in strategies.load_all():
        argv = s.argv(game_tcp=True, game_udp=True)
        chunks, cur = [], []
        for a in argv[1:]:
            if a == "--new":
                chunks.append(cur)
                cur = []
            else:
                cur.append(a)
        chunks.append(cur)
        for c in chunks:
            if any("ipset-games.txt" in a for a in c):
                tcp = any(a.startswith("--filter-tcp") for a in c)
                assert any("ipset-exclude-steam.txt" in a for a in c) == tcp, (s.name, c[:3])
    monkeypatch.setattr(paths, "LISTS", tmp_path)
    paths.sync_lists()
    assert "155.133.224.0/19" in (tmp_path / "ipset-exclude-steam.txt").read_text().split()

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

def test_auto_hostlist_is_its_own_last_profile():
    """A --hostlist-auto profile takes every 80/443 connection: inside the general profile it kept Cloudflare/AWS
    from the ipset-all profile and the «16 KB» block stalled them (Wardogs' Epic login)."""
    s = strategies.Strategy("t", "80,443", "443", [
        strategies.Profile(["--filter-tcp=80,443", "--hostlist=%LISTS%list-general.txt", "--dpi-desync=fake"]),
        strategies.Profile(["--filter-tcp=80,443", "--ipset=%LISTS%ipset-all.txt", "--dpi-desync=multisplit"])])
    a = s.argv(auto_hostlist=True)
    profiles = " ".join(a).split(" --new ")
    assert "--hostlist-auto" not in profiles[0] and "ipset-all" in profiles[1]
    last = profiles[-1]
    assert "--hostlist-auto=" in last and "list-general" not in last and "--dpi-desync=fake" in last
    assert "--hostlist-auto" not in " ".join(s.argv(auto_hostlist=False))

def test_slim_lists(tmp_path):
    from app import runner
    def lst(name, text):
        f = tmp_path / name
        f.write_text(text, encoding="utf-8")
        return str(f)
    allf = lst("ipset-all.txt", "1.0.0.0/8\n")
    tg = lst("ipset-telegram.txt", "203.0.113.113/32\n")
    vpn = lst("ipset-exclude-vpn.txt", "203.0.113.113/32\n")
    ex = lst("ipset-exclude.txt", "# comment\n10.0.0.1\n10.0.0.2\n")
    gen = lst("list-general.txt", "discord.com\n")
    genu = lst("list-general-user.txt", "# Never leave this file empty\ndomain.example.abc\n")
    only = lst("list-only.txt", "domain.example.abc\n")
    argv = [r"C:\z\winws.exe", "--wf-udp=443", "--filter-udp=443", f"--ipset={tg}", f"--ipset={allf}",
            f"--ipset-exclude={ex}", f"--ipset-exclude={vpn}", "--dpi-desync=fake", "--new",
            "--filter-tcp=443", f"--hostlist={gen}", f"--hostlist={genu}", "--new", f"--hostlist={only}"]
    out, snap = runner.slim(argv)
    assert out == [r"C:\z\winws.exe", "--wf-udp=443", "--filter-udp=443", f"--ipset={allf}",
                   "--ipset-exclude-ip=10.0.0.1,10.0.0.2", "--dpi-desync=fake", "--new",
                   "--filter-tcp=443", f"--hostlist={gen}", "--new", f"--hostlist={only}"]  # sole include stays
    assert not runner.stale(snap)
    (tmp_path / "ipset-exclude-vpn.txt").write_text("138.124.5.202\n", encoding="utf-8")
    assert runner.stale(snap)  # winws no longer watches it: the app restarts it
    assert runner.slim([r"C:\z\winws2.exe", f"--ipset={tg}"])[0] == [r"C:\z\winws2.exe", f"--ipset={tg}"]

def test_strategies_cached_until_vendor_changes(tmp_path, monkeypatch):
    from app import strategies
    src = next(paths.VENDOR.glob("general.bat")).read_text(encoding="utf-8")
    monkeypatch.setattr(paths, "VENDOR", tmp_path)
    (tmp_path / "general.bat").write_text(src, encoding="utf-8")
    calls = []
    real = strategies.parse_bat
    monkeypatch.setattr(strategies, "parse_bat", lambda p: calls.append(p.name) or real(p))
    first = strategies.load_all()
    parsed = len(calls)
    second = strategies.load_all()
    assert len(calls) == parsed and second is not first        # разбор не повторился, список, копия
    assert [s.name for s in second] == [s.name for s in first]
    (tmp_path / "general.bat").write_text(src + "\nrem touched\n", encoding="utf-8")
    strategies.load_all()
    assert len(calls) > parsed, "после обновления vendor кэш должен устареть"
