"""Merge a ZapretAuto strategy report (GitHub issue body in $ISSUE_BODY) into strategies-db.json.

DB layout: {asn: {zapret_version: {"isp": str, "reports": [...], "choice": {...}}}}
The published choice is the one reported most often (ties: best average success ratio).
Exit code 1 = invalid report (the workflow comments and closes the issue).
"""
import json
import os
import re
import sys
from pathlib import Path

DB = Path(__file__).resolve().parent.parent / "strategies-db.json"
NAME = re.compile(r"^[A-Za-z0-9 ]{1,40}$")
GROUP = re.compile(r"^[a-z]{3}:[0-9a-z,*:+-]{1,160}$")
MAX_REPORTS = 50
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


def merge(db: dict, rep: dict) -> dict:
    e = db.setdefault(rep["asn"], {}).setdefault(rep["zapret"], {"isp": rep["isp"], "reports": []})
    e["isp"] = rep["isp"] or e["isp"]
    e["reports"] = (e["reports"] + [{k: rep[k] for k in ("choice", "score", "total", "app")}])[-MAX_REPORTS:]
    stats: dict[str, list] = {}
    for r in e["reports"]:
        if _ver(r.get("app", "0")) < MIN_APP:
            continue
        key = json.dumps(r["choice"], sort_keys=True)
        stats.setdefault(key, []).append(r["score"] / r["total"])
    if not stats:
        e.pop("choice", None)
        return db
    best = max(stats, key=lambda k: (len(stats[k]), sum(stats[k]) / len(stats[k])))
    e["choice"] = json.loads(best)
    e["votes"] = len(stats[best])
    return db


def main() -> int:
    try:
        rep = parse(os.environ.get("ISSUE_BODY", ""))
    except (ValueError, json.JSONDecodeError) as e:
        print(f"invalid report: {e}")
        return 1
    db = json.loads(DB.read_text(encoding="utf-8")) if DB.exists() else {}
    DB.write_text(json.dumps(merge(db, rep), ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"merged {rep['asn']} {rep['zapret']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
