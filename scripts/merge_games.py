"""Merge a ZapretAuto game-servers report (issue body in $ISSUE_BODY, author in $REPORTER) into games-db.json.

DB: {"votes": {"games": {exe: {ip: [reporter, ...]}}, "web": {net: [reporter, ...]}},
     "games": {exe: [ip, ...]}, "web": [net, ...]}
The published part ("games", "web") holds only addresses sent by >= MIN_VOTES different people: one wrong or
malicious report cannot push addresses into everyone's bypass. Reporters are stored as short hashes.
Exit code 1 = invalid report (the workflow comments and closes the issue).
"""
import hashlib
import ipaddress
import json
import os
import re
import sys
from pathlib import Path
DB = Path(__file__).resolve().parent.parent / "games-db.json"
EXE = re.compile(r"^[a-z0-9][a-z0-9 ._()+-]{0,79}\.exe$")
MIN_VOTES = 2
MAX_GAMES, MAX_IPS, MAX_WEB = 20, 256, 512
MAX_REPORTERS = 20
def _public(ip: str) -> bool:
    try:
        a = ipaddress.IPv4Address(ip)
    except ValueError:
        return False
    return a.is_global and not a.is_multicast
def parse(body: str) -> dict:
    m = re.search(r"```json\s*(\{.*?\})\s*```", body, re.S)
    if not m:
        raise ValueError("no json block")
    r = json.loads(m.group(1))
    games, web = r.get("games") or {}, r.get("web") or []
    if not isinstance(games, dict) or not isinstance(web, list) or len(games) > MAX_GAMES or len(web) > MAX_WEB:
        raise ValueError("bad layout")
    out = {}
    for exe, ips in games.items():
        if not EXE.match(str(exe)) or not isinstance(ips, list) or len(ips) > MAX_IPS:
            raise ValueError(f"bad game {exe!r}")
        if not all(isinstance(ip, str) and _public(ip) for ip in ips):
            raise ValueError(f"bad ip for {exe}")
        out[exe] = sorted(set(ips))
    for net in web:
        if not (isinstance(net, str) and net.endswith(".0/24") and _public(net[:-3])):
            raise ValueError(f"bad net {net!r}")
    if not out and not web:
        raise ValueError("empty report")
    return {"games": out, "web": sorted(set(web))}
def _vote(table: dict, key: str, who: str) -> None:
    v = table.setdefault(key, [])
    if who not in v:
        table[key] = (v + [who])[-MAX_REPORTERS:]
def merge(db: dict, rep: dict, reporter: str) -> dict:
    who = hashlib.sha256(reporter.lower().encode()).hexdigest()[:12]
    votes = db.setdefault("votes", {"games": {}, "web": {}})
    for exe, ips in rep["games"].items():
        for ip in ips:
            _vote(votes["games"].setdefault(exe, {}), ip, who)
    for net in rep["web"]:
        _vote(votes["web"], net, who)
    db["games"] = {exe: sorted(ip for ip, v in t.items() if len(v) >= MIN_VOTES)
                   for exe, t in sorted(votes["games"].items())}
    db["games"] = {k: v[:MAX_IPS] for k, v in db["games"].items() if v}
    db["web"] = sorted(n for n, v in votes["web"].items() if len(v) >= MIN_VOTES)[:MAX_WEB]
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
    print(f"merged {len(rep['games'])} games, {len(rep['web'])} nets")
    return 0
if __name__ == "__main__":
    sys.exit(main())
