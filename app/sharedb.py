"""Shared strategy database: proven picks per ISP (ASN), crowd-sourced via GitHub issues.

Read:  strategies-db.json from the app repo (raw.githubusercontent).
Write: the user opens a prefilled issue; .github/workflows/strategy-db.yml merges it into the DB.
Only the ASN and ISP name are shared, never the IP address.
"""
import json
import re
from urllib.parse import quote

import requests

from .updater import APP_REPO

DB_URL = f"https://raw.githubusercontent.com/{APP_REPO}/main/strategies-db.json"


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
        db = requests.get(DB_URL, timeout=8).json()
    except (requests.RequestException, ValueError):
        return None
    e = db.get(asn, {}).get(zapret_version)
    return e.get("choice") if e else None


def report_url(asn: str, name: str, zapret_version: str, app_version: str, choice: dict,
               score: int, total: int) -> str:
    body = {"asn": asn, "isp": name, "zapret": zapret_version, "app": app_version,
            "choice": choice, "score": score, "total": total}
    text = ("Автоматический отчёт ZapretAuto. Не меняйте блок ниже.\n\n```json\n"
            + json.dumps(body, ensure_ascii=False, indent=1) + "\n```\n")
    title = f"[report] {asn} {name}"[:120]
    return f"https://github.com/{APP_REPO}/issues/new?title={quote(title)}&body={quote(text)}"
