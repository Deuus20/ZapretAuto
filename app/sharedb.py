"""Shared strategy database: proven picks per ISP (ASN), crowd-sourced via GitHub issues.

Read:  strategies-db.json from the app repo (raw.githubusercontent).
Write: the user opens a prefilled issue; .github/workflows/strategy-db.yml merges it into the DB.
Only the ASN and ISP name are shared, never the IP address.
"""
import json
import re
from urllib.parse import quote

import requests

from .updater import APP_REPO, app_raw_urls

DB_FILE = "strategies-db.json"


def _get_json(name: str):
    """A JSON file from our repo: GitHub, else the Codeberg mirror. Raises if neither answers."""
    err: Exception = ValueError("no source")
    for url in app_raw_urls(name):
        try:
            r = requests.get(url, timeout=8)
            r.raise_for_status()
            return r.json()
        except (requests.RequestException, ValueError) as e:
            err = e
    raise err


def isp() -> tuple[str, str] | None:
    """(ASN like 'AS25513', ISP name) of the current connection."""
    try:
        org = requests.get("https://ipinfo.io/json", timeout=8).json().get("org", "")
    except (requests.RequestException, ValueError):
        return None
    m = re.match(r"(AS\d+)\s*(.*)", org)
    return (m.group(1), m.group(2).strip()) if m else None


def suggestion(asn: str, zapret_version: str) -> dict | None:
    """Best known choice {"base", "picks"} for this ISP and zapret version, if any."""
    try:
        db = _get_json(DB_FILE)
    except (requests.RequestException, ValueError):
        return None
    e = db.get(asn, {}).get(zapret_version)
    return e.get("choice") if e else None


def isp_entry(asn: str, zapret_version: str) -> dict | None:
    """What other users of this ISP reported for this zapret version: {"votes", "reports", "choice"};
    None when the base is unreachable."""
    try:
        db = _get_json(DB_FILE)
    except (requests.RequestException, ValueError):
        return None
    e = db.get(asn, {}).get(zapret_version) or {}
    return {"votes": e.get("votes", 0), "reports": len(e.get("reports", [])), "choice": e.get("choice")}


def report_url(asn: str, name: str, zapret_version: str, app_version: str, choice: dict,
               score: int, total: int) -> str:
    body = {"asn": asn, "isp": name, "zapret": zapret_version, "app": app_version,
            "choice": choice, "score": score, "total": total}
    text = ("Автоматический отчёт ZapretAuto. Не меняйте блок ниже.\n\n```json\n"
            + json.dumps(body, ensure_ascii=False, indent=1) + "\n```\n")
    title = f"[report] {asn} {name}"[:120]
    return f"https://github.com/{APP_REPO}/issues/new?title={quote(title)}&body={quote(text)}"


GAMES_FILE = "games-db.json"
MAX_URL = 7500  # GitHub drops prefilled issue text beyond ~8 KB of URL


def games_db() -> dict | None:
    """Crowd-sourced game servers {"games": {exe: [ip]}, "web": [net]}: each address sent by 2+ people."""
    try:
        db = _get_json(GAMES_FILE)
    except (requests.RequestException, ValueError):
        return None
    return db if isinstance(db, dict) else None


def games_report_url(app_version: str, games: dict[str, list[str]], web: list[str]) -> str:
    """Prefilled issue with learned game servers; big lists are trimmed to fit the URL."""
    games = {k: sorted(v) for k, v in games.items() if v}
    web = sorted(web)
    while True:
        body = {"app": app_version, "games": games, "web": web}
        text = ("Автоматический отчёт ZapretAuto: адреса серверов игр. Не меняйте блок ниже.\n\n```json\n"
                + json.dumps(body, separators=(",", ":")) + "\n```\n")
        title = "[games] " + ", ".join(sorted(games))[:100]
        url = f"https://github.com/{APP_REPO}/issues/new?title={quote(title)}&body={quote(text)}"
        if len(url) <= MAX_URL:
            return url
        big = max(games, key=lambda k: len(games[k]), default=None)
        if web and (big is None or len(web) >= len(games[big])):
            web = web[:len(web) // 2]
        elif big is not None:
            games[big] = games[big][:len(games[big]) // 2]
            if not games[big]:
                games.pop(big)
        else:
            return url
