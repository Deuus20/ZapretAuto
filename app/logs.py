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
    """Hide the Windows user name."""
    text = re.sub(r"(?i)(c:[\\/]+users[\\/]+)[^\\/\"']+", r"\1<user>", text)
    return text


def _redact_public(text: str) -> str:
    """For a public GitHub issue: also hide every non-local IPv4 (VPN servers, the user's own address)."""
    import ipaddress

    def ip(m):
        try:
            a = ipaddress.IPv4Address(m.group(0))
        except ValueError:
            return m.group(0)
        return m.group(0) if a.is_private or a.is_loopback else "<ip>"
    return re.sub(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", ip, _redact(text))


ISSUE_URL_MAX = 7500  # GitHub drops prefilled issue text beyond ~8 KB of URL


def issue_url(what: str = "", diag_results: list | None = None) -> str:
    """Prefilled GitHub issue: versions, the user's words, problems found by diagnostics and the log tail
    (as many last lines as fit). Opened in the browser; the user presses «Create»."""
    from urllib.parse import quote
    from . import __version__, tester, updater
    from .updater import APP_REPO
    s = tester.chosen_strategy()
    head = [f"**Что не работает:** {what.strip() or '(не указано)'}", "",
            f"ZapretAuto {__version__} · zapret {updater.current_version()} · Windows {platform.version()}",
            f"Стратегия: {s.name if s else 'не выбрана'} · VPN: {tester.vpn_active() or 'нет'}"]
    probs = [f"- {r['label']}: {r['detail']}" for r in diag_results or [] if r.get("level") != "ok"]
    if probs:
        head += ["", "**Диагностика:**"] + probs
    try:
        lines = (LOG_DIR / "zapretauto.log").read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        lines = []
    title = quote(f"[log] ZapretAuto {__version__}: {(what.strip() or 'не работает')[:60]}")
    n = min(len(lines), 80)
    while True:
        body = "\n".join(head) + "\n\n**Журнал (последние строки):**\n```\n" + "\n".join(lines[-n:] if n else []) + "\n```\n"
        url = f"https://github.com/{APP_REPO}/issues/new?title={title}&body={quote(_redact_public(body))}"
        if len(url) <= ISSUE_URL_MAX or n == 0:
            return url
        n = max(0, n - 10)


def make_report(dest: Path, diag_results: list | None = None) -> Path:
    """Zip logs, diagnostics, settings and test results (user name redacted) for a bug report."""
    from . import __version__, strategies, tester, updater
    dest = Path(dest)
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(LOG_DIR.glob("zapretauto.log*")) + sorted(LOG_DIR.glob("winws.log")):
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
        from . import stats
        info["stability"] = [d for d in stats.daily(14) if d["checks"] or d["crashes"] or d["repicks"]]
        z.writestr("info.json", _redact(json.dumps(info, ensure_ascii=False, indent=1)))
        if diag_results is not None:
            z.writestr("diagnostics.json", _redact(json.dumps(diag_results, ensure_ascii=False, indent=1)))
    return dest
