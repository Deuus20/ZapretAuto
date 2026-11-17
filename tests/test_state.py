"""Настройки, статистика, журнал, пользовательские списки."""
import time
from pathlib import Path

import pytest

from app import lists, paths, settings, stats


def test_settings_atomic_and_corrupt_tolerant(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "DATA", tmp_path)
    settings._cache = None
    settings.save_settings(a=1)
    settings.save_settings(b=2)
    assert settings.load_settings() == {"a": 1, "b": 2}
    (tmp_path / "settings.json").write_text("{broken", encoding="utf-8")
    settings._cache = None
    assert settings.load_settings() == {}
    assert (tmp_path / "settings.json.bad").exists()

def test_stats_uptime_and_checks(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "DATA", tmp_path)
    t = [1000.0]
    monkeypatch.setattr(stats.time, "time", lambda: t[0] + 86400 * 100)
    stats.record("start")
    t[0] += 3600
    stats.record("check", ok=True)
    stats.record("check", ok=False)
    stats.record("stop")
    s = stats.summary(7)
    assert s["uptime_h"] == 1.0 and s["checks"] == 2 and s["ok_pct"] == 50

def test_settings_with_bom(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "DATA", tmp_path)
    settings._cache = None
    (tmp_path / "settings.json").write_text('{"a": 1}', encoding="utf-8-sig")
    assert settings.load_settings() == {"a": 1}

def test_user_domain_add_remove(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "LISTS", tmp_path)
    (tmp_path / "list-general-user.txt").write_text("# Never leave this file empty\ndomain.example.abc", encoding="utf-8")
    assert lists.add_user_domain("Example.org") is True
    assert lists.add_user_domain("example.org") is False  # case-insensitive duplicate
    text = (tmp_path / "list-general-user.txt").read_text(encoding="utf-8")
    assert text.splitlines()[-1] == "Example.org" and "abcExample" not in text  # added on its own line
    lists.remove_user_domain("EXAMPLE.org")
    assert "example" not in (tmp_path / "list-general-user.txt").read_text(encoding="utf-8").lower().replace(
        "domain.example.abc", "")

def test_user_exclude_list_never_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "LISTS", tmp_path)
    assert lists.add_user_domain("bank.ru", "list-exclude-user.txt")
    assert (tmp_path / "list-exclude-user.txt").read_text(encoding="utf-8") == "bank.ru\n"
    lists.remove_user_domain("bank.ru", "list-exclude-user.txt")
    assert (tmp_path / "list-exclude-user.txt").read_text(encoding="utf-8") == "domain.example.abc\n"  # winws needs 1

def test_stats_daily_counts_problems(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "DATA", tmp_path)
    stats.record("check", ok=True)
    stats.record("check", ok=False)
    stats.record("crash")
    stats.record("repick", reason="x")
    days = stats.daily(7)
    assert len(days) == 7 and days[-1]["day"] == time.strftime("%d.%m")
    assert {k: days[-1][k] for k in ("checks", "fails", "crashes", "repicks")} == \
        {"checks": 2, "fails": 1, "crashes": 1, "repicks": 1}
    assert all(d["checks"] == 0 for d in days[:-1])

def test_settings_nested_edit_does_not_leak_into_cache(monkeypatch, tmp_path):
    from app import paths, settings
    monkeypatch.setattr(paths, "DATA", tmp_path)
    settings.save_settings(strategy={"base": "a", "picks": {}})
    settings.load_settings()["strategy"]["picks"]["Discord"] = "x"  # edited, never saved
    assert settings.load_settings()["strategy"]["picks"] == {}

def test_first_run_defaults_only_on_fresh_install(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "DATA", tmp_path)
    settings._cache = None
    assert settings.first_run() and settings.load_settings() == settings.FIRST_RUN
    settings.save_settings(game_watch=False)
    assert not settings.first_run() and settings.load_settings()["game_watch"] is False   # user's choice kept

def test_locked_settings_are_not_wiped(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "DATA", tmp_path)
    settings._cache = None
    settings.save_settings(strategy={"base": "ALT8"}, a=1)
    settings._cache = None
    real = Path.read_text
    monkeypatch.setattr(Path, "read_text", lambda self, *a, **k: (_ for _ in ()).throw(PermissionError("locked"))
                        if self.name == "settings.json" else real(self, *a, **k))
    monkeypatch.setattr(settings.time, "sleep", lambda s: None)
    assert settings.load_settings() == {}                                  # transient: not quarantined
    with pytest.raises(OSError):
        settings.save_settings(b=2)                                        # refuses to write over unread settings
    monkeypatch.setattr(Path, "read_text", real)
    assert settings.load_settings() == {"strategy": {"base": "ALT8"}, "a": 1}
    assert not (tmp_path / "settings.json.bad").exists()

def test_stats_uptime_without_stop_and_locked_file(tmp_path, monkeypatch):
    import json as _json

    monkeypatch.setattr(paths, "DATA", tmp_path)
    now = time.time()
    ev = [{"t": now - 7200, "k": "start"}, {"t": now - 6000, "k": "check", "ok": True},   # killed, PC off
          {"t": now - 600, "k": "start"}, {"t": now - 300, "k": "stop"}]
    (tmp_path / "stats.json").write_text(_json.dumps(ev), encoding="utf-8")
    assert stats.summary()["uptime_h"] == round((1200 + 300) / 3600, 1)
    from pathlib import Path
    monkeypatch.setattr(Path, "read_text", lambda self, *a, **k: (_ for _ in ()).throw(PermissionError("locked")))
    stats.record("crash")                                                  # no exception, history kept
    monkeypatch.undo()
    assert len(_json.loads((tmp_path / "stats.json").read_text(encoding="utf-8"))) == 4
