"""Диагностика, hosts, WinDivert, каталог данных."""

import pytest

from app import paths, tester, updater


def test_diag_reports_missing_bypass_files(tmp_path, monkeypatch):
    from app import diag
    (tmp_path / "winws.exe").write_text("x")
    monkeypatch.setattr(paths, "BIN", tmp_path)
    assert set(diag.missing_files()) == {"WinDivert.dll", "WinDivert64.sys", "cygwin1.dll"}

def test_diag_console_output_in_oem_codepage():
    """sc/net/powershell print in the OEM code page: decoding as UTF-8 gave «������» in the dialog."""
    from app import diag
    msg = "Служба не найдена"
    assert diag._decode(msg.encode("cp866"), oem=866) == msg     # sc.exe output
    assert diag._decode(msg.encode("utf-8"), oem=866) == msg     # a tool that prints UTF-8

def test_diag_never_deletes_windivert_in_use(monkeypatch):
    """Game flow capture keeps the driver loaded: deleting it in use fails with 1072 and blocks WinDivert until reboot."""
    from app import diag
    monkeypatch.setattr(diag, "_own_windivert", lambda: True)
    monkeypatch.setattr(diag, "_run", lambda *a, **k: (_ for _ in ()).throw(AssertionError("sc must not run")))
    out = diag.apply_fix("remove_windivert")
    assert "не нужно" in out and "слежение за играми" in out

def test_diag_windivert_delete_pending_is_explained(monkeypatch):
    from app import diag
    monkeypatch.setattr(diag, "_own_windivert", lambda: False)
    def run(args, timeout=20):
        if args[1] == "stop":
            return 0, ""
        return (1072, "[SC] DeleteService: служба помечена на удаление") if args[-1] == "WinDivert" else (1060, "нет")
    monkeypatch.setattr(diag, "_run", run)
    out = diag.apply_fix("remove_windivert")
    assert "код 1072" in out and "перезагруз" in out.lower() and "1060" not in out

def test_dns_poison_detection(monkeypatch):
    import socket

    from app import diag
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
    q = {t.key: t for t in tester.load_targets() if t.url.startswith("h3://")}
    assert q["YouTubeQUIC"].group.startswith("udp:443:")
    assert q["DiscordQUIC"].group == "udp:443:list-general"  # discord.com is in Flowseal's general list

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

def test_hosts_block_update_and_remove(tmp_path, monkeypatch):
    from app import diag
    hosts = tmp_path / "hosts"
    hosts.write_bytes(b"\xef\xbb\xbf# Copyright Microsoft\r\n88.210.52.47 zapret-tracker.duckdns.org\r\n"
                      b"1.1.1.1 discord.com\r\n10.0.0.5 myserver.lan\r\n")
    monkeypatch.setattr(diag, "_hosts_file", lambda: hosts)
    monkeypatch.setattr(diag, "_run", lambda c, timeout=20: (0, ""))
    entries = [("162.159.135.232", "discord.com")] + [(f"149.154.167.{i}", f"t{i}.telegram.org") for i in range(12)]
    monkeypatch.setattr(diag, "flowseal_hosts", lambda: entries)
    assert diag.hosts_state(entries) == (0, False)
    assert "13" in diag.apply_fix("hosts_update")
    raw = hosts.read_bytes()
    text = raw.decode("utf-8-sig")
    assert raw.startswith(b"\xef\xbb\xbf") and "\r\n" in text                    # encoding and line ends kept
    assert "1.1.1.1 discord.com" not in text and "162.159.135.232 discord.com" in text  # stale line replaced
    assert "zapret-tracker.duckdns.org" in text and "myserver.lan" in text       # foreign lines untouched
    assert diag.hosts_state(entries) == (13, True)
    diag.apply_fix("hosts_update")                                                # twice: still one block
    assert hosts.read_bytes().decode("utf-8-sig").count(diag.HOSTS_BEGIN) == 1
    diag.apply_fix("hosts_remove")
    text = hosts.read_bytes().decode("utf-8-sig")
    assert diag.HOSTS_BEGIN not in text and "telegram" not in text and "myserver.lan" in text
    assert (tmp_path / "hosts.zapretauto.bak").exists()  # копия на случай сбоя записи
    assert not (tmp_path / "hosts.zapretauto.tmp").exists()  # временный файл переименован, а не оставлен

def test_smart_app_control_reported(monkeypatch):
    from app import diag
    monkeypatch.setattr(diag, "_smart_app_control", lambda: 1)
    monkeypatch.setattr(diag, "flowseal_hosts", lambda: (_ for _ in ()).throw(OSError("offline")))
    monkeypatch.setattr(diag, "dns_poisoned", lambda: None)
    monkeypatch.setattr(diag, "_quic_blocked", lambda: False)
    monkeypatch.setattr(diag, "_ps", lambda cmd: (0, "0"))            # no real PowerShell / services: fast
    monkeypatch.setattr(diag, "_run", lambda c, timeout=20: (0, ""))
    monkeypatch.setattr(diag, "_svc_status", lambda n: "Running" if n == "BFE" else None)
    rows = {r["label"]: r for r in diag.run_checks()}
    assert rows["Smart App Control"]["level"] == "err"
    assert "Smart App Control" in diag.first_problem()            # also the cause shown when winws won't start

def test_diagnostics_read_services_and_doh_in_two_calls(monkeypatch):
    """Раньше прогон диагностики запускал 10 процессов PowerShell только на службы; теперь, один."""
    from app import diag
    calls = []
    def fake_ps(name):
        calls.append(name)
        return {"services": (0, "BFE=Running\nzapret=Stopped\nWinDivert=Running"),
                "timestamps": (0, "enabled"),
                "doh": (0, "doh=2;auto=2;dns=1.1.1.1 8.8.8.8")}[name]
    monkeypatch.setattr(diag, "_ps", fake_ps)
    monkeypatch.setattr(diag, "_procs", lambda: set())
    monkeypatch.setattr(diag, "missing_files", lambda: [])
    monkeypatch.setattr(diag, "flowseal_hosts", lambda: [])
    monkeypatch.setattr(diag, "dns_poisoned", lambda: [])
    monkeypatch.setattr(diag, "_browser_doh_on", lambda: True)
    monkeypatch.setattr(diag, "_discord_caches", lambda: [])
    monkeypatch.setattr(diag, "_quic_disabled", lambda: True)
    rows = {r["label"]: r for r in diag.run_checks()}
    assert calls.count("services") == 1 and sorted(set(calls)) == ["doh", "services", "timestamps"]
    assert rows["Base Filtering Engine"]["level"] == "ok" and rows["Служба zapret"]["level"] == "ok"
    assert rows["Secure DNS (DoH)"]["level"] == "ok"
    with pytest.raises(ValueError):  # чужое имя службы в сценарий PowerShell не подставляем
        diag._svc_status("BFE'; rm -rf /; '")

def test_diagnostics_cache_expires_after_fix(monkeypatch):
    from app import diag
    calls = []
    monkeypatch.setattr(diag, "_procs", lambda: calls.append(1) or set())
    monkeypatch.setattr(diag, "_ps", lambda name: (0, "doh=1;auto=2;dns="))
    monkeypatch.setattr(diag, "missing_files", lambda: [])
    monkeypatch.setattr(diag, "flowseal_hosts", lambda: [])
    monkeypatch.setattr(diag, "dns_poisoned", lambda: [])
    monkeypatch.setattr(diag, "_browser_doh_on", lambda: True)
    monkeypatch.setattr(diag, "_discord_caches", lambda: [])
    monkeypatch.setattr(diag, "_quic_disabled", lambda: True)
    first = diag.run_checks()
    assert diag.run_checks() is first and len(calls) == 1
    diag.forget()  # после «Исправить» состояние надо проверить заново
    assert diag.run_checks() is not first and len(calls) == 2

def test_programdata_staging_is_fail_closed(tmp_path, monkeypatch):
    """ProgramData-каталог может создать любой пользователь: файлы обновления туда не качаем, пока не заперли."""
    from app import paths
    monkeypatch.setenv("PROGRAMDATA", str(tmp_path / "pd"))
    monkeypatch.setattr(paths, "DATA", tmp_path / "pd" / "ZapretAuto" / "data")
    paths.DATA.mkdir(parents=True)
    monkeypatch.setattr(paths, "DATA_LOCKED", False)
    assert not paths.staging_trusted()
    with pytest.raises(RuntimeError, match="не защищён"):
        updater._stage("x")
    paths._set_dacl(paths.DATA)  # «заперли» каталог: недостаточно флага, проверяется фактический DACL
    monkeypatch.setattr(paths, "DATA_LOCKED", True)
    assert paths.staging_trusted()
    monkeypatch.setattr(type(paths.DATA), "is_junction", lambda self: True)  # ссылка на чужую папку
    assert not paths.staging_trusted() and paths._suspicious(paths.DATA) == "это junction"
    monkeypatch.setattr(paths, "_suspicious", lambda d: "это junction")
    assert paths._lock_down(tmp_path) is False  # ACL на ссылку не вешаем

def test_own_folder_does_not_need_acl(tmp_path, monkeypatch):
    from app import paths
    monkeypatch.setenv("PROGRAMDATA", str(tmp_path / "pd"))
    monkeypatch.setattr(paths, "DATA", tmp_path / "app" / "data")  # портативная копия: папка своя
    paths.DATA.mkdir(parents=True)
    monkeypatch.setattr(paths, "DATA_LOCKED", False)
    assert paths.staging_trusted()

def test_windivert_loaded_from_data_copy(monkeypatch, tmp_path):
    from app import runner
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    (bin_ / "WinDivert.dll").write_bytes(b"dll v1")
    (bin_ / "WinDivert64.sys").write_bytes(b"sys v1")
    monkeypatch.setattr(runner.paths, "BIN", bin_)
    monkeypatch.setattr(runner.paths, "DATA", tmp_path / "data")
    loaded = []
    monkeypatch.setattr(runner.ctypes, "WinDLL", lambda p, **kw: loaded.append(p) or p, raising=False)
    first = runner.windivert_dll()
    assert not first.startswith(str(bin_)) and open(first, "rb").read() == b"dll v1"
    assert (tmp_path / "data" / "windivert").joinpath(first).parent.joinpath("WinDivert64.sys").read_bytes() == b"sys v1"
    assert runner.windivert_dll() == first  # same content: same copy
    (bin_ / "WinDivert.dll").write_bytes(b"dll v2")  # zapret updated
    second = runner.windivert_dll()
    assert second != first and open(second, "rb").read() == b"dll v2"
    assert len(list((tmp_path / "data" / "windivert").iterdir())) == 1  # old copy cleaned

def test_flowseal_hosts_only_known_sites(monkeypatch):
    import requests

    from app import diag
    good = "".join(f"1.2.3.{i} gateway{i}.discord.com\n" for i in range(10))
    text = good + "6.6.6.6 sberbank.ru\n6.6.6.6 evildiscord.com\n6.6.6.6 discord.com.evil.net\n"
    monkeypatch.setattr(requests, "get", lambda *a, **k: type("R", (), {"text": text})())
    hosts = [h for _, h in diag.flowseal_hosts()]
    assert len(hosts) == 10 and all(h.endswith(".discord.com") for h in hosts)

def test_hosts_block_without_end_keeps_user_lines():
    from app import diag
    lines = ["127.0.0.1 a", diag.HOSTS_BEGIN, "1.1.1.1 ours", "10.0.0.1 mine"]
    assert diag._strip_block(lines) == ["127.0.0.1 a", "1.1.1.1 ours", "10.0.0.1 mine"]
