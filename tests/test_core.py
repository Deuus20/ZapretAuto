"""Остальные тесты проекта: целиком офлайн, без winws и сети."""
import importlib.util
from pathlib import Path

from app import paths

ROOT = Path(__file__).resolve().parent.parent

def test_winws_errors_explained(tmp_path, monkeypatch):
    from app import runner
    assert "WinDivert" in runner.explain("windivert: error opening filter: 577 Windows cannot verify the signature")
    assert "администратора" in runner.explain("windivert: error opening filter: Access is denied. (5)")
    assert "антивирус" in runner.explain("windivert: error opening filter: The system cannot find the file (2)")
    assert "другой обход" in runner.explain("A copy of winws is already running with the same filter")
    assert "list-general.txt" in runner.explain("cannot access hostlist file 'C:/x/list-general.txt'")
    assert runner.explain("windivert initialized. capture is started.") is None
    monkeypatch.setattr(paths, "DATA", tmp_path)
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs" / "winws.log").write_bytes(b"\n".join(b"line %d" % i for i in range(50)))
    assert runner.last_output(3) == "line 47\nline 48\nline 49"

def test_quiet_detects_calls_not_idle_messengers(monkeypatch):
    from types import SimpleNamespace as NS

    from app import quiet
    class P:
        def __init__(self, pid):
            self.pid = pid
        def name(self):
            return {1: "Discord.exe", 2: "chrome.exe"}[self.pid]
    monkeypatch.setattr(quiet.psutil, "Process", P)
    idle = [NS(pid=1, laddr=NS(ip="0.0.0.0", port=5353)), NS(pid=2, laddr=NS(ip="0.0.0.0", port=50000)),
            NS(pid=1, laddr=NS(ip="127.0.0.1", port=6000))]
    assert quiet.call_app(idle) is None
    assert quiet.call_app(idle + [NS(pid=1, laddr=NS(ip="0.0.0.0", port=61234))]) == "discord"
    assert quiet.busy_reason(game=True) == "идёт игра"

def test_codemap_is_current():
    spec = importlib.util.spec_from_file_location("codemap", ROOT / "tools" / "codemap.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    assert (ROOT / "CODEMAP.md").read_text(encoding="utf-8") == m.build(), "run: python tools/codemap.py"

def test_runtime_lock_has_hashes_for_every_pin():
    """CI ставит runtime по requirements.lock с --require-hashes: пакет без хэшей или с другой версией ломает сборку."""
    def norm(name: str) -> str:
        return name.lower().replace("_", "-")
    pins = {}
    for line in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "==" in line:
            name, _, ver = line.partition("==")
            pins[norm(name)] = ver.strip()
    assert pins, "в requirements.txt нет закреплённых версий"
    lock = (ROOT / "requirements.lock").read_text(encoding="utf-8")
    for name, ver in pins.items():
        block = lock.split(f"\n{name}=={ver}", 1)
        assert len(block) == 2, f"{name}=={ver} нет в requirements.lock — пересоберите lock"
        assert "--hash=sha256:" in block[1].split("\n\n", 1)[0], f"у {name}=={ver} нет хэшей"

