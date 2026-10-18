"""Repeatable game-login test matrix: each config N times, interleaved, incl. a no-bypass baseline.

Usage: python tools/game_matrix.py <steam_appid> <process_prefix> <rounds> <out.json> [configs_json]
Login success = the game holds LOGIN_SERVERS+ distinct ESTABLISHED servers within WAIT seconds.
"""
import json
import os
import sys
import time
from pathlib import Path

import psutil

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import strategies  # noqa: E402
from app import winkeys  # noqa: E402
from app.runner import Runner  # noqa: E402

APPID, PREFIX, ROUNDS, OUT = sys.argv[1], sys.argv[2].lower(), int(sys.argv[3]), Path(sys.argv[4])
LOGIN_SERVERS, WAIT = 5, 80
G, I = "tcp:80,443:list-general", "tcp:80,443,8443:ipset-all"
CONFIGS = {
    "none": None,
    "good": ({"base": "FAKE TLS AUTO ALT", "picks": {I: "ALT11", G: "ALT11"}}, True),
    "ALT11": ({"base": "ALT11", "picks": {}}, False),
    "FTAA": ({"base": "FAKE TLS AUTO ALT", "picks": {}}, False),
}
if len(sys.argv) > 5:  # optional JSON {"name": {"base":..,"picks":{..}} | null}; auto-hostlist on
    CONFIGS = {k: (None if v is None else (v, True)) for k, v in json.loads(sys.argv[5]).items()}
by = {s.name: s for s in strategies.load_all()}


def kill():
    for p in psutil.process_iter(["name"]):
        n = (p.info["name"] or "").lower()
        if n.startswith(PREFIX) or n == "zapretauto.exe":  # the app would restart winws and spoil the test
            try:
                p.kill()
            except psutil.Error:
                pass


def servers():
    ips = set()
    for p in psutil.process_iter(["name"]):
        n = (p.info["name"] or "").lower()
        if n.startswith(PREFIX) and "launcher" not in n:
            try:
                ips |= {c.raddr.ip for c in p.net_connections("inet")
                        if c.raddr and c.status == "ESTABLISHED" and not c.raddr.ip.startswith("127.")}
            except psutil.Error:
                pass
    return len(ips)


res = json.loads(OUT.read_text()) if OUT.exists() else {}
r = Runner()
for rnd in range(ROUNDS):
    names = list(CONFIGS)
    names = names[rnd % len(names):] + names[:rnd % len(names)]  # rotate order each round
    for name in names:
        kill()
        time.sleep(6)
        cfg = CONFIGS[name]
        if cfg is None:
            r.stop()
        else:
            c, auto = cfg
            s = strategies.mix("m", {g: by[n] for g, n in c["picks"].items()}, by[c["base"]])
            r.start(s, True, True, auto)
            time.sleep(2)
        os.startfile(f"steam://rungameid/{APPID}")
        t0, best = time.time(), 0
        while time.time() - t0 < WAIT:
            winkeys.press_any_key(PREFIX)  # the game waits for "press any key" before logging in
            best = max(best, servers())
            if best >= LOGIN_SERVERS:
                break
            time.sleep(2)
        ok = best >= LOGIN_SERVERS
        res.setdefault(name, []).append({"ok": ok, "servers": best, "t": round(time.time() - t0), "at": time.strftime("%H:%M")})
        OUT.write_text(json.dumps(res, indent=1))
        print(f"round {rnd + 1} {name}: {'OK' if ok else 'stuck'} ({best})", flush=True)
kill()
r.stop()
print({k: f"{sum(x['ok'] for x in v)}/{len(v)}" for k, v in res.items()})
