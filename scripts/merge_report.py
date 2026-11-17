"""Merge a ZapretAuto strategy report (issue body in $ISSUE_BODY, author in $REPORTER) into strategies-db.json.

DB layout: {asn: {zapret_version: {"isp": str, "reports": [...], "choice": {...}}}}
The published choice is the one reported most often by *different* authors (ties: best average success ratio).
A choice needs at least MIN_AUTHORS different people: one account cannot promote a strategy for a whole ISP.
Exit code 1 = invalid report (the workflow comments and closes the issue).
"""
import hashlib
import json
import os
import re
import sys
from pathlib import Path

DB = Path(__file__).resolve().parent.parent / "strategies-db.json"
NAME = re.compile(r"^[A-Za-z0-9 ]{1,40}$")
GROUP = re.compile(r"^[a-z]{3}:[0-9a-z,*:+-]{1,160}$")
MAX_REPORTS = 50
MAX_PER_AUTHOR = 5  # один автор не должен заполонить историю провайдера
MIN_AUTHORS = 2     # одна учётная запись не может продавить стратегию для всех
LEGACY_AUTHOR = "legacy"  # отчёты без автора (до этой версии): все считаются одним голосом, а не разными
MIN_APP = (1, 0, 8)  # earlier apps probed with a Python TLS fingerprint: their picks do not reflect browsers/Discord
def parse(body: str) -> dict:
    m = re.search(r"```json\s*(\{.*?\})\s*```", body, re.S)
    if not m:
        raise ValueError("no json block")
    r = json.loads(m.group(1))
    if not re.fullmatch(r"AS\d{1,10}", str(r.get("asn", ""))):
        raise ValueError("bad asn")
    if not re.fullmatch(r"v?\d+(\.\d+)*[a-z]?", str(r.get("zapret", ""))):
        raise ValueError("bad zapret version")
    c = r.get("choice") or {}
    picks = c.get("picks") or {}
    if not NAME.match(str(c.get("base", ""))) or not isinstance(picks, dict) or len(picks) > 20:
        raise ValueError("bad choice")
    for g, n in picks.items():
        if not GROUP.match(str(g)) or not NAME.match(str(n)):
            raise ValueError("bad pick")
    score, total = int(r.get("score", -1)), int(r.get("total", 0))
    if not (0 < total <= 1000 and 0 <= score <= total) or score / total < 0.6:
        raise ValueError("score too low or invalid")
    app = str(r.get("app", "0"))
    if not re.fullmatch(r"v?\d+(\.\d+)*", app):
        raise ValueError("bad app version")
    return {"asn": r["asn"], "isp": str(r.get("isp", ""))[:80], "zapret": r["zapret"], "app": app.lstrip("v"),
            "choice": {"base": c["base"], "picks": dict(sorted(picks.items()))}, "score": score, "total": total}
def _ver(v: str) -> tuple:
    return tuple(int(x) for x in re.findall(r"\d+", str(v))[:3]) or (0,)
def _who(reporter: str) -> str:
    """Автор хранится коротким хешем: для подсчёта голосов имя не нужно (как в games-db)."""
    return hashlib.sha256((reporter or "?").lower().encode()).hexdigest()[:12]
def merge(db: dict, rep: dict, reporter: str = "?") -> dict:
    e = db.setdefault(rep["asn"], {}).setdefault(rep["zapret"], {"isp": rep["isp"], "reports": []})
    e["isp"] = rep["isp"] or e["isp"]
    who = _who(reporter)
    reports = e["reports"] + [{**{k: rep[k] for k in ("choice", "score", "total", "app")}, "who": who}]
    mine = [i for i, r in enumerate(reports) if r.get("who") == who]
    if len(mine) > MAX_PER_AUTHOR:  # старые отчёты этого автора вытесняются его новыми
        drop = set(mine[:-MAX_PER_AUTHOR])
        reports = [r for i, r in enumerate(reports) if i not in drop]
    e["reports"] = reports[-MAX_REPORTS:]
    stats: dict[str, tuple[set[str], list[float]]] = {}
    for r in e["reports"]:
        if _ver(r.get("app", "0")) < MIN_APP:
            continue
        key = json.dumps(r["choice"], sort_keys=True)
        authors, scores = stats.setdefault(key, (set(), []))
        authors.add(r.get("who") or LEGACY_AUTHOR)
        scores.append(r["score"] / r["total"])
    if not stats:
        e.pop("choice", None)
        e["votes"] = 0
        return db
    best = max(stats, key=lambda k: (len(stats[k][0]), sum(stats[k][1]) / len(stats[k][1])))
    e["votes"] = len(stats[best][0])
    if e["votes"] < MIN_AUTHORS:  # один голос — ещё не выбор провайдера
        e.pop("choice", None)
        return db
    e["choice"] = json.loads(best)
    return db
def main() -> int:
    try:
        rep = parse(os.environ.get("ISSUE_BODY", ""))
    except (ValueError, json.JSONDecodeError) as e:
        print(f"invalid report: {e}")
        return 1
    db = json.loads(DB.read_text(encoding="utf-8")) if DB.exists() else {}
    DB.write_text(json.dumps(merge(db, rep, os.environ.get("REPORTER", "?")), ensure_ascii=False, indent=1) + "\n",
                  encoding="utf-8")
    print(f"merged {rep['asn']} {rep['zapret']}")
    return 0
if __name__ == "__main__":
    sys.exit(main())
