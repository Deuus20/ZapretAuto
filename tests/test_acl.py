"""Защита каталога данных: DACL проверяется, а не «icacls вернул 0».

icacls /grant:r не убирает явные ACE, которые успели поставить заранее, поэтому каталог в ProgramData
мог остаться доступным на запись другому пользователю — и в него качался бы установщик, запускаемый
от администратора.
"""
import subprocess

import pytest

from app import paths


def _dir(tmp_path):
    d = tmp_path / "locked"
    d.mkdir()
    return d


def test_permissive_acl_is_replaced_and_verified(tmp_path):
    d = _dir(tmp_path)
    assert paths._writable_by_others(d) is True             # свежий каталог: пишут все
    assert paths._data_dir_safe(d) is False
    paths._set_dacl(d)
    assert paths._writable_by_others(d) is False
    assert paths._data_dir_safe(d) is True
    assert d.is_dir() and list(d.iterdir()) == []           # читать и перечислять по-прежнему можно


def test_explicit_everyone_ace_is_removed(tmp_path):
    d = _dir(tmp_path)
    subprocess.run(["icacls", str(d), "/grant", "*S-1-1-0:(OI)(CI)F"],
                   check=True, capture_output=True, timeout=30)
    assert paths._writable_by_others(d) is True              # именно то, что пропускал старый _lock_down
    paths._set_dacl(d)
    assert paths._writable_by_others(d) is False


def test_lock_down_requires_admin(tmp_path):
    d = _dir(tmp_path)
    if paths._is_admin():
        pytest.skip("проверка имеет смысл без прав администратора")
    assert paths._lock_down(d) is False


def test_staging_trusted_rechecks_a_tampered_acl(tmp_path, monkeypatch):
    d = _dir(tmp_path)
    monkeypatch.setattr(paths, "DATA", d)
    monkeypatch.setattr(paths, "DATA_LOCKED", True)
    monkeypatch.setattr(paths, "_shared_dir", lambda p: True)
    assert paths.staging_trusted() is False
    paths._set_dacl(d)
    assert paths.staging_trusted() is True


def test_suspicious_dir_is_never_trusted(tmp_path, monkeypatch):
    d = _dir(tmp_path)
    monkeypatch.setattr(paths, "DATA", d)
    monkeypatch.setattr(paths, "DATA_LOCKED", True)
    monkeypatch.setattr(paths, "_shared_dir", lambda p: True)
    paths._set_dacl(d)
    monkeypatch.setattr(type(d), "is_junction", lambda self: True)
    assert paths.staging_trusted() is False
