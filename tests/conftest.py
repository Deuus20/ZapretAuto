import socket

import pytest


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    """Tests are offline: a real connection is a bug (slow, flaky, depends on the network)."""
    def refuse(self, *a, **k):
        raise OSError("network disabled in tests")
    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
    for name in ("sendto", "sendmsg"):  # UDP мимо connect(): gamewatch и диагностика могли бы «проверить сеть»
        if hasattr(socket.socket, name):
            monkeypatch.setattr(socket.socket, name, refuse)
    for name in ("gethostbyname", "gethostbyname_ex", "gethostbyaddr", "getaddrinfo"):
        monkeypatch.setattr(socket, name,
                            lambda *a, **k: (_ for _ in ()).throw(socket.gaierror("offline")))
    _no_libcurl(monkeypatch)


def _no_libcurl(monkeypatch):
    """curl_cffi ходит в сеть мимо Python-сокетов: без этой заглушки тест мог выйти в интернет незаметно."""
    try:
        from curl_cffi import requests as cr
    except Exception:  # noqa: BLE001 (не установлен: и хорошо, тесту нечего подменять)
        return

    def refuse(*a, **k):
        raise OSError("network disabled in tests")

    for name in ("get", "post", "request", "head"):
        if callable(getattr(cr, name, None)):
            monkeypatch.setattr(cr, name, refuse)
    if hasattr(cr, "Session"):
        monkeypatch.setattr(cr.Session, "request", refuse, raising=False)
@pytest.fixture(autouse=True)
def _fresh_diag_cache():
    """Диагностика кэширует прогон на 10 с: иначе следующий тест получил бы прошлый результат."""
    from app import diag, netcache
    diag.forget()
    netcache.reset()  # ETag-кэш GitHub: иначе следующий тест увидит ответ предыдущего
    yield
    diag.forget()
    netcache.reset()
@pytest.fixture(autouse=True)
def _isolated_data(tmp_path, monkeypatch):
    """Ни один тест не пишет в рабочую копию: data/ и data/lists/ — временные.

    Иначе прогон тестов менял настоящие settings.json (отметка обновления ipset) и list-auto.txt, а
    `git status` после тестов показывал runtime-мусор."""
    from app import paths, settings
    data = tmp_path / "data"
    lists = data / "lists"
    lists.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(paths, "DATA", data)
    monkeypatch.setattr(paths, "LISTS", lists)
    settings._cache = None
    yield
    settings._cache = None
