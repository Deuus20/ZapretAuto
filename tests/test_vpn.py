"""VPN: адреса серверов и исключения."""

from app import paths, tester, vpn


def test_vpn_route_parsing(monkeypatch):
    rows = [("0.0.0.0", "0.0.0.0", "192.168.1.1", "192.168.1.70"),
            ("45.132.253.182", "255.255.255.255", "192.168.1.1", "192.168.1.70"),
            ("10.8.1.0", "255.255.255.0", "On-link", "10.8.1.3")]
    monkeypatch.setattr(vpn, "_routes", lambda: rows)
    monkeypatch.setattr(vpn, "_adapter_of", lambda ip: "Ethernet" if ip == "192.168.1.70" else "AmneziaVPN")
    monkeypatch.setattr(vpn.psutil, "net_connections", lambda kind: [])
    assert vpn.server_ips() == {"45.132.253.182"}
    monkeypatch.setattr(vpn, "_direct_blocked", lambda ip: False)
    assert vpn.bind_ip() == "192.168.1.70"
    monkeypatch.setattr(vpn, "_direct_blocked", lambda ip: True)  # kill switch: no way around the VPN
    assert vpn.bind_ip() is None
    monkeypatch.setattr(tester, "vpn_active", lambda: "AmneziaVPN")
    monkeypatch.setattr(tester, "egress_ip", lambda: "10.8.1.4")
    assert tester.quick_check() == {}  # no false «не работает» while the VPN blocks the direct path
    monkeypatch.setattr(tester, "egress_ip", lambda: None)
    monkeypatch.setattr(tester, "vpn_active", lambda: None)
    assert tester.quick_check() == {}  # offline: nothing to check
    assert vpn.VPN_RE.search("AmneziaVPN") and not vpn.VPN_RE.search("Laptop LAN")

def test_vpn_exclude_file(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "LISTS", tmp_path)
    monkeypatch.setattr(vpn, "server_ips", lambda: {"45.132.253.182"})
    vpn.write_exclude()
    assert (tmp_path / "ipset-exclude-vpn.txt").read_text().split() == ["45.132.253.182"]
    monkeypatch.setattr(vpn, "server_ips", lambda: set())
    vpn.write_exclude()
    assert (tmp_path / "ipset-exclude-vpn.txt").read_text().split() == [vpn.PLACEHOLDER]  # never empty

def test_vpn_exclude_not_rewritten_when_unchanged(tmp_path, monkeypatch):
    import os
    monkeypatch.setattr(paths, "LISTS", tmp_path)
    monkeypatch.setattr(vpn, "server_ips", lambda: {"45.132.253.182"})
    vpn.write_exclude()
    f = tmp_path / "ipset-exclude-vpn.txt"
    os.utime(f, (1, 1))
    vpn.write_exclude()
    assert f.stat().st_mtime == 1  # untouched: winws would otherwise reload it every minute

def test_restart_discord_after_vpn(monkeypatch):
    from types import SimpleNamespace

    from app import vpn
    procs = [SimpleNamespace(info={"name": "Discord.exe", "exe": r"C:\D\app-1\Discord.exe"}),
             SimpleNamespace(info={"name": "Discord.exe", "exe": r"C:\D\app-1\Discord.exe"}),
             SimpleNamespace(info={"name": "steam.exe", "exe": r"C:\S\steam.exe"})]
    killed, started = [], []
    monkeypatch.setattr(vpn.psutil, "process_iter", lambda attrs: procs if not killed else [])
    monkeypatch.setattr(vpn.subprocess, "run", lambda cmd, **kw: killed.append(cmd[-1]))
    monkeypatch.setattr(vpn.subprocess, "Popen", lambda cmd, **kw: started.append(cmd))
    assert vpn.restart_discord() == ["discord.exe"]
    assert killed == ["discord.exe"]
    assert started == [["explorer.exe", r"C:\D\app-1\Discord.exe"]]  # unelevated, once per build
    killed.clear(), started.clear()
    procs[:] = procs[2:]
    assert vpn.restart_discord() == [] and not started  # Discord wasn't running: don't start it

def test_vpn_left_out_of_capture(monkeypatch, tmp_path):
    from app import runner
    monkeypatch.setattr(runner.paths, "DATA", tmp_path)
    def fake_save(cmd, **kw):  # winws --wf-save
        open(cmd[-1].split("=", 1)[1], "w").write("outbound and udp.DstPort >= 1024\n")
    monkeypatch.setattr(runner.subprocess, "run", fake_save)
    monkeypatch.setattr(runner, "compiles", lambda flt: True)
    argv = [r"C:\z\winws.exe", "--wf-tcp=443", "--wf-udp=443,1024-65535", "--filter-udp=443", "--new"]
    out = runner.uncapture(argv, {"138.124.5.202"})
    f = tmp_path / "logs" / "winws-filter.txt"
    assert out == [r"C:\z\winws.exe", "--filter-udp=443", "--new", f"--wf-raw=@{f}"]
    assert "and (ipv6 or (ip.SrcAddr != 138.124.5.202 and ip.DstAddr != 138.124.5.202))" in f.read_text()
    monkeypatch.setattr(runner, "compiles", lambda flt: False)
    assert runner.uncapture(argv, {"138.124.5.202"}) == argv  # WinDivert would reject it: keep the bypass up
    assert runner.uncapture(argv, set()) == argv  # no VPN: filter as winws builds it
    assert runner.uncapture([r"C:\z\winws2.exe", "--wf-udp-out=443"], {"1.2.3.4"})[1] == "--wf-udp-out=443"

def test_vpn_process_names():
    assert vpn.is_vpn_proc("Throne.exe") and vpn.is_vpn_proc("AmneziaVPN.exe") and vpn.is_vpn_proc("happ.exe")
    assert not vpn.is_vpn_proc("ThroneAndLiberty.exe") and not vpn.is_vpn_proc("whatsapp.exe")
