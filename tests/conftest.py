import socket

import pytest


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    """Tests are offline: a real connection is a bug (slow, flaky, depends on the network)."""
    def refuse(self, *a, **k):
        raise OSError("network disabled in tests")
    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
    monkeypatch.setattr(socket, "gethostbyname", lambda *a, **k: (_ for _ in ()).throw(socket.gaierror("offline")))
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: (_ for _ in ()).throw(socket.gaierror("offline")))
