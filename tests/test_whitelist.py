"""Белые списки: вердикт по проверкам без обхода (сеть в тестах не нужна)."""
import pytest

from app import tester, whitelist


@pytest.mark.parametrize("allowed_ok,blocked_ok,want", [
    (0, 0, "offline"),      # ничего не открывается: кабель/VPN, а не список
    (0, 2, "offline"),
    (5, 1, "ok"),           # заблокированный сайт открылся напрямую: фильтра по списку нет
    (3, 0, "whitelist"),
    (5, 0, "whitelist"),
    (1, 0, "unknown"),      # один ответ из пяти: это шум, а не вердикт
])
def test_verdict(allowed_ok, blocked_ok, want):
    assert whitelist.verdict(allowed_ok, len(whitelist.ALLOWED), blocked_ok) == want


def test_check_probes_both_groups_directly(monkeypatch):
    good = ("gosuslugi", "nalog", "ya.ru", "vk.com", "mail.ru")

    def probe_all(ts):
        return {t.key: (0.1 if any(g in t.url for g in good) else None) for t in ts}
    monkeypatch.setattr(tester, "probe_all", probe_all)
    monkeypatch.setattr(tester, "BIND", "10.0.0.1")  # адрес VPN прошлой проверки не должен остаться
    rep = whitelist.check()
    assert rep["verdict"] == "whitelist"
    assert all(rep["allowed"].values()) and not any(rep["blocked"].values())
    assert tester.BIND is None


def test_check_reports_no_allowlist_when_a_blocked_site_opens(monkeypatch):
    def probe_all(ts):
        return {t.key: 0.1 for t in ts}
    monkeypatch.setattr(tester, "probe_all", probe_all)
    assert whitelist.check()["verdict"] == "ok"
