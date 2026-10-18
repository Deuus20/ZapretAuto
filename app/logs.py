"""File logging, crash capture and the "save problem report" zip."""
import json
import logging
import logging.handlers
import platform
import re
import sys
import threading
import time
import traceback
import zipfile
from pathlib import Path

from . import paths

LOG_DIR = paths.DATA / "logs"
log = logging.getLogger("zapretauto")
_on_crash = None  # callable(str) set by the GUI to show a message


def setup() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    h = logging.handlers.RotatingFileHandler(LOG_DIR / "zapretauto.log", maxBytes=1_000_000, backupCount=3,
                                             encoding="utf-8")
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(h)
    log.setLevel(logging.INFO)
    sys.excepthook = _excepthook
    threading.excepthook = lambda a: _excepthook(a.exc_type, a.exc_value, a.exc_traceback)
    from . import __version__
    log.info("start ZapretAuto %s, Windows %s", __version__, platform.version())


def _excepthook(t, v, tb) -> None:
    text = "".join(traceback.format_exception(t, v, tb))
    log.error("unhandled exception:\n%s", text)
    if _on_crash:
        try:
            _on_crash(f"{t.__name__}: {v}")
        except Exception:  # noqa: BLE001
            pass


def _redact(text: str) -> str:
    """Hide the Windows user name and public IPv4 addresses (except well-known service IPs)."""
    text = re.sub(r"(?i)(c:[\\/]+users[\\/]+)[^\\/\"']+", r"\1<user>", text)
    return text


def make_report(dest: Path, diag_results: list | None = None) -> Path:
    """Zip logs, diagnostics, settings and test results (user name redacted) for a bug report."""
    from . import __version__, strategies, tester, updater
    dest = Path(dest)
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(LOG_DIR.glob("zapretauto.log*")):
            z.writestr(f"logs/{f.name}", _redact(f.read_text(encoding="utf-8", errors="replace")))
        for name in ("settings.json", "results.json", "stats.json"):
            f = paths.DATA / name
            if f.exists():
                z.writestr(name, _redact(f.read_text(encoding="utf-8", errors="replace")))
        info = {"app": __version__, "zapret": updater.current_version(), "windows": platform.version(),
                "time": time.strftime("%Y-%m-%d %H:%M:%S"), "vpn": tester.vpn_active(),
                "egress": tester.egress_adapter()}
        s = tester.chosen_strategy()
        if s:
            info["winws_argv"] = s.argv()
        info["strategies"] = [x.name for x in strategies.load_all()]
        z.writestr("info.json", _redact(json.dumps(info, ensure_ascii=False, indent=1)))
        if diag_results is not None:
            z.writestr("diagnostics.json", _redact(json.dumps(diag_results, ensure_ascii=False, indent=1)))
    return dest
