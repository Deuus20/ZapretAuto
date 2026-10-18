"""Probe targets through each strategy, score them, build the best mix."""
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import psutil
import requests

from . import paths, strategies
from .runner import Runner

READ_BYTES = 32 * 1024  # DPI often freezes TLS sessions after ~16 KB
TIMEOUT = 5


@dataclass
class Target:
    key: str
    url: str

    @property
    def host(self) -> str:
        return re.sub(r"^https?://([^/]+).*", r"\1", self.url)


VPN_RE = re.compile(r"vpn|wireguard|wg|amnezia|tun|tap|outline|nekoray|hiddify", re.I)


def egress_adapter() -> str | None:
    """Name of the adapter that carries traffic to the internet."""
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.connect(("8.8.8.8", 53))
        ip = s.getsockname()[0]
    return next((n for n, addrs in psutil.net_if_addrs().items() if any(a.address == ip for a in addrs)), None)


def vpn_active() -> str | None:
    a = egress_adapter()
    return a if a and VPN_RE.search(a) else None


def load_targets() -> list[Target]:
    out = []
    for line in (paths.VENDOR / "utils" / "targets.txt").read_text(encoding="utf-8").splitlines():
        m = re.match(r'\s*(\w+)\s*=\s*"(https?://[^"]+)"', line)
        if m:
            out.append(Target(*m.groups()))
    return out


def _domains(name: str) -> set[str]:
    p = paths.LISTS / name
    return {l.strip() for l in p.read_text(encoding="utf-8").splitlines() if l.strip() and not l.startswith("#")} if p.exists() else set()


def target_group(t: Target) -> str:
    """TCP profile group that handles this target's host."""
    def hit(ds):
        return any(t.host == d or t.host.endswith("." + d) for d in ds)
    if hit(_domains("list-google.txt")):
        return "tcp:443:list-google"
    if hit(_domains("list-general.txt") | _domains("list-general-user.txt")):
        return "tcp:80,443:list-general"
    return "tcp:80,443,8443:ipset-all"


def probe(t: Target) -> float | None:
    """Seconds to read READ_BYTES (or full body), None on failure."""
    t0 = time.perf_counter()
    try:
        with requests.get(t.url, timeout=TIMEOUT, stream=True, proxies={"http": None, "https": None},
                          headers={"User-Agent": "Mozilla/5.0"}) as r:
            got = 0
            for chunk in r.iter_content(4096):
                got += len(chunk)
                if got >= READ_BYTES or time.perf_counter() - t0 > TIMEOUT * 2:
                    break
        return time.perf_counter() - t0
    except requests.RequestException:
        return None


def probe_all(targets: list[Target]) -> dict[str, float | None]:
    with ThreadPoolExecutor(8) as ex:
        return dict(zip((t.key for t in targets), ex.map(probe, targets)))


def score(res: dict[str, float | None]) -> tuple[int, float]:
    ok = [v for v in res.values() if v is not None]
    return len(ok), -(sum(ok) / len(ok) if ok else 99)


def test_strategy(runner: Runner, s: strategies.Strategy, targets, warmup: float = 2.0) -> dict | None:
    runner.start(s)
    time.sleep(warmup)
    if not runner.running:
        runner.stop()
        return None
    res = probe_all(targets)
    runner.stop()
    return res


def auto_pick(log=print) -> dict:
    """Test every strategy, mix the best profile per group, save to data/settings.json."""
    runner, targets = Runner(), load_targets()
    paths.sync_lists()
    all_s = strategies.load_all()
    groups = {t.key: target_group(t) for t in targets}

    results = {"_baseline": probe_all(targets)}
    log(f"baseline: {score(results['_baseline'])[0]}/{len(targets)}")
    if vpn := vpn_active():
        log(f"VPN '{vpn}' carries traffic: results are meaningless, aborting")
        return {}
    if score(results["_baseline"])[0] == len(targets):
        log("nothing blocked without zapret; picking by latency only")
    for s in all_s:
        r = test_strategy(runner, s, targets)
        results[s.name] = r
        log(f"{s.name}: " + ("winws failed" if r is None else f"{score(r)[0]}/{len(targets)}"))

    tested = {s.name: s for s in all_s if results[s.name]}
    best = max(tested.values(), key=lambda s: score(results[s.name]))
    picks = {}
    for g in set(groups.values()):
        keys = [k for k, v in groups.items() if v == g]
        winner = max(tested.values(), key=lambda s: score({k: results[s.name][k] for k in keys}))
        picks[g] = winner.name
    mixed = strategies.mix("mix", {g: tested[n] for g, n in picks.items()}, best)
    results["mix"] = test_strategy(runner, mixed, targets)
    log(f"mix {picks}: {score(results['mix'])[0]}/{len(targets)}" if results["mix"] else "mix: winws failed")

    use_mix = results["mix"] and score(results["mix"]) > score(results[best.name])
    choice = {"base": best.name, "picks": picks if use_mix else {}}
    paths.DATA.mkdir(exist_ok=True)
    (paths.DATA / "results.json").write_text(json.dumps(results, indent=1), encoding="utf-8")
    save_settings(strategy=choice)
    log(f"chosen: {choice}")
    return choice


def load_settings() -> dict:
    p = paths.DATA / "settings.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def save_settings(**kw) -> None:
    s = load_settings() | kw
    (paths.DATA / "settings.json").write_text(json.dumps(s, indent=1, ensure_ascii=False), encoding="utf-8")


def chosen_strategy() -> strategies.Strategy | None:
    c = load_settings().get("strategy")
    if not c:
        return None
    by = {s.name: s for s in strategies.load_all()}
    base = by.get(c["base"])
    if base and c.get("picks"):
        return strategies.mix("mix", {g: by[n] for g, n in c["picks"].items() if n in by}, base)
    return base


if __name__ == "__main__":
    auto_pick()
