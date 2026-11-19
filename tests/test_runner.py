"""Runner: какой winws считается своим и добивается при остановке."""
from app import paths, runner


def test_same_file_compares_files_not_strings(tmp_path):
    """Короткий путь 8.3 или другой регистр - тот же файл; другой файл - нет."""
    exe = tmp_path / "winws.exe"
    exe.write_bytes(b"x")
    other = tmp_path / "other.exe"
    other.write_bytes(b"y")
    assert runner._same_file(str(exe), exe)
    assert runner._same_file(str(exe).upper(), exe)  # регистр не важен
    assert not runner._same_file(str(other), exe)
    assert not runner._same_file(str(exe), tmp_path / "missing.exe")  # файла нет: сравниваем строки


def test_stray_winws_does_not_match_a_foreign_bin(tmp_path, monkeypatch):
    """winws из другой папки не наш: остановка обхода не должна снимать чужой процесс."""
    monkeypatch.setattr(paths, "BIN", tmp_path / "bin")
    (tmp_path / "bin").mkdir()
    assert runner._stray_winws() is False  # живых winws из нашей папки нет
