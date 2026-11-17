"""Целостность vendor: манифест sha256, сверка и мягкий режим для старых копий."""
from app import vendorcheck


def _vendor(tmp_path):
    (tmp_path / "bin").mkdir()
    (tmp_path / "bin" / "winws.exe").write_bytes(b"MZ real")
    (tmp_path / "bin" / "WinDivert.dll").write_bytes(b"dll")
    (tmp_path / "general.bat").write_text("@echo off\n", encoding="utf-8")
    (tmp_path / "VERSION").write_text("1.10.4\n", encoding="utf-8")
    return tmp_path


def test_manifest_covers_bin_and_strategies(tmp_path):
    v = _vendor(tmp_path)
    assert vendorcheck.write_manifest(v) == 4
    text = vendorcheck.manifest_path(v).read_text(encoding="utf-8")
    assert "bin/winws.exe" in text and "general.bat" in text and "VERSION" in text
    assert "lists/" not in text  # списки меняются службой ipset: их не проверяем
    assert vendorcheck.changed(v) == []


def test_tampered_winws_is_reported(tmp_path):
    v = _vendor(tmp_path)
    vendorcheck.write_manifest(v)
    (v / "bin" / "winws.exe").write_bytes(b"MZ evil")
    assert vendorcheck.changed(v) == ["bin/winws.exe"]


def test_missing_or_extra_file_is_reported(tmp_path):
    v = _vendor(tmp_path)
    vendorcheck.write_manifest(v)
    (v / "bin" / "WinDivert.dll").unlink()
    assert vendorcheck.changed(v) == ["bin/WinDivert.dll"]
    (v / "bin" / "evil.exe").write_bytes(b"MZ evil")
    assert vendorcheck.changed(v) == ["bin/WinDivert.dll", "bin/evil.exe"]


def test_locked_settings_do_not_break_startup(tmp_path, monkeypatch):
    """Занятый settings.json (антивирус, редактор) не должен мешать проверке файлов при старте."""
    from app import settings, vendorcheck
    v = _vendor(tmp_path)
    vendorcheck.write_manifest(v)
    (v / "bin" / "winws.exe").write_bytes(b"MZ evil")
    monkeypatch.setattr(settings, "save_settings",
                        lambda **kw: (_ for _ in ()).throw(PermissionError("locked")))
    assert vendorcheck.ensure(v) == ["bin/winws.exe"]


def test_removed_manifest_is_not_silently_rebaselined(tmp_path):
    """Манифест уже видели, а теперь его нет: это не «старая копия», а способ отключить проверку."""
    v = _vendor(tmp_path)
    assert vendorcheck.ensure(v) is None
    vendorcheck.manifest_path(v).unlink()
    assert vendorcheck.ensure(v) == ["MANIFEST.sha256"]


def test_first_run_writes_manifest_instead_of_warning(tmp_path):
    """Обновление со старой версии: манифеста нет — создаём по текущему состоянию, а не пугаем пользователя."""
    v = _vendor(tmp_path)
    assert vendorcheck.ensure(v) is None
    assert vendorcheck.manifest_path(v).exists()
    assert vendorcheck.ensure(v) == []


def test_diagnostics_reports_tampered_files(tmp_path, monkeypatch):
    from app import diag, paths
    v = _vendor(tmp_path)
    vendorcheck.write_manifest(v)
    (v / "bin" / "winws.exe").write_bytes(b"MZ evil")
    monkeypatch.setattr(paths, "VENDOR", v)
    monkeypatch.setattr(diag, "_ps", lambda name: (0, "doh=1;auto=2;dns="))
    monkeypatch.setattr(diag, "_procs", lambda: set())
    monkeypatch.setattr(diag, "missing_files", lambda: [])
    monkeypatch.setattr(diag, "flowseal_hosts", lambda: [])
    monkeypatch.setattr(diag, "dns_poisoned", lambda: [])
    monkeypatch.setattr(diag, "_browser_doh_on", lambda: True)
    monkeypatch.setattr(diag, "_discord_caches", lambda: [])
    monkeypatch.setattr(diag, "_quic_disabled", lambda: True)
    rows = {r["label"]: r for r in diag.run_checks()}
    assert rows["Файлы обхода изменены"]["level"] == "err"
    assert rows["Файлы обхода изменены"]["fix"] == "reinstall_vendor"
