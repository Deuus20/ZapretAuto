"""Общие помощники тестов: окно, временные подписи, разбор баз."""
import base64
import importlib.util
import json
import os
import time
from pathlib import Path

import pytest

from app import paths, settings

ROOT = Path(__file__).resolve().parent.parent

def _merge_mod():
    spec = importlib.util.spec_from_file_location("merge_report", ROOT / "scripts" / "merge_report.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m
def _body(**over):
    r = {"asn": "AS25513", "isp": "MGTS", "zapret": "1.10.3", "app": "1.0.8",
         "choice": {"base": "ALT12", "picks": {"tcp:443:list-google": "FAKE TLS AUTO ALT", "udp:other": "ALT3"}},
         "score": 23, "total": 28} | over
    return "text\n```json\n" + json.dumps(r) + "\n```\n"
def _fake_pick_env(monkeypatch, tmp_path, baseline_ok, vpn=None, bind=None):
    from app import tester
    from app import vpn as vpnmod
    monkeypatch.setattr(paths, "DATA", tmp_path)
    monkeypatch.setattr(paths, "LISTS", tmp_path / "lists")
    (tmp_path / "lists").mkdir(exist_ok=True)
    settings._cache = None
    targets = [tester.Target(f"T{i}", f"https://t{i}.example") for i in range(10)]
    monkeypatch.setattr(tester, "load_targets", lambda: targets)
    monkeypatch.setattr(tester, "probe_all",
                        lambda ts: {t.key: (0.1 if i < baseline_ok else None) for i, t in enumerate(ts)})
    monkeypatch.setattr(tester, "vpn_active", lambda: vpn)
    monkeypatch.setattr(vpnmod, "bind_ip", lambda: bind)
    monkeypatch.setattr(paths, "sync_lists", lambda: None)
    from app import diag
    monkeypatch.setattr(diag, "dns_poisoned", lambda: [])  # keep tests offline
    from app import sharedb
    monkeypatch.setattr(sharedb, "isp", lambda: ("AS100", "Home ISP"))
    return tester
def _games_mod():
    spec = importlib.util.spec_from_file_location("merge_games", ROOT / "scripts" / "merge_games.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m
def _gui(tmp_path, monkeypatch, **st):
    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setattr(paths, "DATA", tmp_path)
    monkeypatch.setattr(paths, "LISTS", tmp_path / "lists")
    settings._cache = None
    settings.save_settings(strategy={"base": "ALT8", "picks": {}}, ipset_updated=time.time(), **st)
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from app.gui.window import MainWindow
    return app, MainWindow()
def _close(app, w):
    w.tray.hide()
    w._timer.stop()
    w.deleteLater()
    from PySide6.QtCore import QCoreApplication, QEvent
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)  # else every old window stays alive
    app.processEvents()                                                  # and each restyle repolishes them all
def _patch_keys(monkeypatch, seed):
    from app import ed25519, signing
    pub = ed25519.public_key(seed)
    monkeypatch.setattr(signing, "KEYS", {signing.key_id(pub): base64.b64encode(pub).decode()})
    return pub
class _Resp:
    """Ответ requests: тело в память (iter_content для потоковой загрузки)."""
    def __init__(self, url, body=b"", js=None):
        self.url, self._body, self._js = url, body, js
        self.status_code = 200
        self.headers = {"Content-Length": str(len(body))}
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False
    def raise_for_status(self):
        pass
    def json(self):
        return self._js
    def iter_content(self, size):
        for i in range(0, len(self._body), size):
            yield self._body[i:i + size]
def _release_net(tag, files):
    """requests.get для релиза: API отдаёт assets по именам файлов, сами файлы — из files."""
    def get(url, **kw):
        if url.endswith("/releases/latest"):
            return _Resp(url, js={"tag_name": tag, "assets": [
                {"name": n, "browser_download_url": f"https://github.com/x/{n}"} for n in files]})
        return _Resp(url, body=files[url.rsplit("/", 1)[-1]])
    return get
