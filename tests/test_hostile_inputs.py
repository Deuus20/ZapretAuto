"""Враждебные входы: чего программа не должна делать, даже если данные пришли плохие.

Отдельный файл, потому что это регрессионная защита найденных когда-то дыр: zip-slip и zip-бомба в архиве
обновления, мусорные адреса в списке ipset, IPv6 среди игровых адресов, подменённая копия WinDivert.
"""
import io
import zipfile
from pathlib import Path

import pytest

from app import gamewatch, lists, paths, updater


def _zip_bytes(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)
    return buf.getvalue()


@pytest.mark.parametrize("name", ["../evil.txt", "a/../../evil.txt", "C:/Windows/evil.txt"])
def test_safe_extract_refuses_paths_outside_the_dir(tmp_path, name):
    zpath = tmp_path / "x.zip"
    zpath.write_bytes(_zip_bytes({name: b"pwned"}))
    out = tmp_path / "out"
    out.mkdir()
    with zipfile.ZipFile(zpath) as z, pytest.raises(RuntimeError, match="unsafe path"):
        updater._safe_extract(z, out)
    assert not (tmp_path / "evil.txt").exists() and not (tmp_path.parent / "evil.txt").exists()


def test_safe_extract_refuses_zip_bomb(tmp_path):
    """Заявленный размер распакованного содержимого больше лимита: иначе один архив забьёт диск."""
    zpath = tmp_path / "bomb.zip"
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("big.bin", b"\0" * (updater.MAX_DOWNLOAD * 3 + 1024))
    out = tmp_path / "out"
    with zipfile.ZipFile(zpath) as z, pytest.raises(RuntimeError, match="archive too large"):
        updater._safe_extract(z, out)


def test_safe_extract_keeps_normal_files(tmp_path):
    zpath = tmp_path / "ok.zip"
    zpath.write_bytes(_zip_bytes({"bin/winws.exe": b"MZ", "lists/ipset-all.txt": b"1.2.3.0/24\n"}))
    out = tmp_path / "out"
    with zipfile.ZipFile(zpath) as z:
        updater._safe_extract(z, out)
    assert (out / "bin" / "winws.exe").read_bytes() == b"MZ"


class _FakeResp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()
        return False


def _serve(monkeypatch, text: str):
    def urlopen(req, timeout=30):
        return _FakeResp(text.encode("utf-8"))
    monkeypatch.setattr(lists.urllib.request, "urlopen", urlopen)


def test_update_ipset_rejects_garbage_and_keeps_the_old_file(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "LISTS", tmp_path)
    (tmp_path / "ipset-all.txt").write_text("10.0.0.0/8\n", encoding="utf-8")
    logs = []
    # каждая строка «похожа на адрес», но неверна: регулярка их пропускала, ipaddress, нет
    _serve(monkeypatch, "10.1.0.0/16\n999.999.999.999\n1:2:3\n10.0.0.0/999\n")
    assert lists.update_ipset(logs.append) is False
    assert (tmp_path / "ipset-all.txt").read_text(encoding="utf-8") == "10.0.0.0/8\n"
    assert any("некорректные строки" in m for m in logs)


def test_update_ipset_rejects_html_injection(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "LISTS", tmp_path)
    _serve(monkeypatch, "10.1.0.0/16\n<script>alert(1)</script>\n")
    assert lists.update_ipset(lambda m: None) is False
    assert not (tmp_path / "ipset-all.txt").exists()


def test_update_ipset_accepts_ipv6_and_marks_the_file_custom(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "LISTS", tmp_path)
    _serve(monkeypatch, "10.1.0.0/16\n2a00:1450:4001::/48\n")
    assert lists.update_ipset(lambda m: None) is True
    assert (tmp_path / "ipset-all.txt.custom").exists()  # sync_lists не вернёт файл к копии vendor
    assert "2a00:1450:4001::/48" in (tmp_path / "ipset-all.txt").read_text(encoding="utf-8")


def test_ipv6_is_not_taken_for_a_game_server():
    assert gamewatch.public_ipv4("1.2.3.4") is True
    for ip in ("2a00:1450:4001::1", "fe80::1", "::1", "10.0.0.1", "not-an-ip"):
        assert gamewatch.public_ipv4(ip) is False


def test_ipv6_never_reaches_the_game_pipeline():
    """Фильтр стоит в самом конвейере, а не только в public_ipv4: IPv6-адрес не должен стать «сервером»."""
    assert gamewatch._is_server_ip("1.2.3.4", 5000, set()) is True
    for ip in ("2a00:1450:4001::1", "::ffff:1.2.3.4", "fe80::1"):
        assert gamewatch._is_server_ip(ip, 5000, set()) is False
    assert gamewatch._is_server_ip("1.2.3.4", 53, set()) is False      # DNS игры, не её сервер
    assert gamewatch._is_server_ip("1.2.3.4", 5000, {"1.2.3.4"}) is False  # откатанный сервер


def test_tampered_windivert_copy_is_replaced(tmp_path, monkeypatch):
    """WinDivert.dll грузится из data/: подменённую копию нельзя отдавать в админский процесс."""
    from app import runner
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "WinDivert.dll").write_bytes(b"real dll")
    (bin_dir / "WinDivert64.sys").write_bytes(b"driver")
    monkeypatch.setattr(paths, "BIN", bin_dir)
    monkeypatch.setattr(paths, "DATA", tmp_path / "data")
    loaded = []
    monkeypatch.setattr(runner.ctypes, "WinDLL", lambda p, **kw: loaded.append(p) or object())
    runner.windivert_dll()
    dll = Path(loaded[-1])
    dll.write_bytes(b"evil dll")
    runner.windivert_dll()
    assert Path(loaded[-1]).read_bytes() == b"real dll"
