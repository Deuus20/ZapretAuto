"""Probe targets through each strategy, score them, build the best mix."""
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import psutil
import requests

from . import paths, strategies
from .settings import load_settings, save_settings  # noqa: F401 (re-exported)
from .runner import Runner

READ_BYTES = 32 * 1024  # DPI often freezes TLS sessions after ~16 KB
TIMEOUT = 4
ROUNDS = 2
WARMUP = 1.5


@dataclass
class Target:
    key: str
    url: str
    group: str | None = None  # fixed group (UDP targets); None = derive from lists

    @property
    def host(self) -> str:
        return re.sub(r"^[a-z]+://([^/:]+).*", r"\1", self.url)

    @property
    def is_stun(self) -> bool:
        return self.url.startswith("stun://")


VPN_RE = re.compile(r"vpn|wireguard|amnezia|outline|nekoray|hiddify|^(wg|tun|tap)\d*$|\b(tun|tap)\b", re.I)


def egress_adapter() -> str | None:
    """Name of the adapter that carries traffic to the internet."""
    import socket
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 53))
            ip = s.getsockname()[0]
    except OSError:  # no network / no route
        return None
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
    out += STUN_TARGETS
    return out


STUN_TARGETS = [
    Target("STUN_Google19302", "stun://stun.l.google.com:19302", "udp:19294-19344:discord,stun"),
    Target("STUN_Cloudflare3478", "stun://stun.cloudflare.com:3478", "udp:other"),
]


def probe_stun(t: Target, timeout: float = 2.0, tries: int = 2) -> float | None:
    """RFC5389 Binding Request over UDP; RTT seconds or None."""
    import os
    import socket
    import struct
    host, _, port = t.url[len("stun://"):].partition(":")
    for _ in range(tries):
        tid = os.urandom(12)
        req = struct.pack("!HHI", 0x0001, 0, 0x2112A442) + tid
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.settimeout(timeout)
                addr = (socket.gethostbyname(host), int(port))
                t0 = time.perf_counter()
                s.sendto(req, addr)
                while (left := timeout - (time.perf_counter() - t0)) > 0:
                    s.settimeout(left)
                    data, _a = s.recvfrom(2048)
                    if len(data) >= 20:
                        typ, _ln, cookie = struct.unpack("!HHI", data[:8])
                        if typ == 0x0101 and cookie == 0x2112A442 and data[8:20] == tid:
                            return time.perf_counter() - t0
        except OSError:
            pass
    return None


def _domains(name: str) -> set[str]:
    p = paths.LISTS / name
    return {l.strip() for l in p.read_text(encoding="utf-8").splitlines() if l.strip() and not l.startswith("#")} if p.exists() else set()


def target_group(t: Target) -> str:
    """Profile group that handles this target's host."""
    if t.group:
        return t.group
    def hit(ds):
        return any(t.host == d or t.host.endswith("." + d) for d in ds)
    if hit(_domains("list-google.txt")):
        return "tcp:443:list-google"
    if hit(_domains("list-general.txt") | _domains("list-general-user.txt")):
        return "tcp:80,443:list-general"
    return "tcp:80,443,8443:ipset-all"


BLOCK_PAGE = re.compile(r"warning\.rt\.ru|blocked|zapret-info|rkn\.gov|eais", re.I)


def probe(t: Target) -> float | None:
    """Seconds to read READ_BYTES (or full body), None on failure."""
    if t.is_stun:
        return probe_stun(t)
    t0 = time.perf_counter()
    try:
        with requests.get(t.url, timeout=TIMEOUT, stream=True, proxies={"http": None, "https": None},
                          headers={"User-Agent": "Mozilla/5.0"}) as r:
            if r.status_code == 451 or BLOCK_PAGE.search(r.url):  # ISP stub page
                return None
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


def test_strategy(runner: Runner, s: strategies.Strategy, targets, warmup: float = WARMUP) -> dict | None:
    try:
        runner.start(s)
        time.sleep(warmup)
        if not runner.running:
            return None
        res = probe_all(targets)
        return res if runner.running else None  # winws died during probing
    finally:
        runner.stop()


QUICK = {"Discord": "DiscordMain", "YouTube": "YouTubeWeb", "Голос Discord": "STUN_Google19302"}


def quick_check() -> dict[str, bool]:
    """Check key services through the currently running bypass (does not touch winws)."""
    ts = [t for t in load_targets() if t.key in QUICK.values()]
    res = probe_all(ts)
    return {name: res.get(key) is not None for name, key in QUICK.items()}


def auto_pick(log=print) -> dict:
    """Test every strategy, mix the best profile per group, save to data/settings.json."""
    runner, targets = Runner(), load_targets()
    paths.sync_lists()
    all_s = strategies.load_all()
    groups = {t.key: target_group(t) for t in targets}

    if vpn := vpn_active():
        log(f"Включён VPN «{vpn}»: весь трафик идёт через него, проверка бессмысленна. "
            "Отключите VPN или настройте раздельное туннелирование.")
        return {}
    results = {"_baseline": probe_all(targets)}
    n = len(targets)
    base_ok = score(results["_baseline"])[0]
    log(f"Без обхода доступно: {base_ok} из {n}")
    if base_ok == 0:
        log("Похоже, интернета нет — подбор отменён.")
        return {}
    for i, s in enumerate(all_s, 1):
        r = test_strategy(runner, s, targets)
        results[s.name] = r
        log(f"[{i}/{len(all_s)}] {s.name}: " + ("не запустилась" if r is None else f"{score(r)[0]} из {n}"))

    tested = {s.name: s for s in all_s if results[s.name]}
    if not tested:
        log("Ни одна стратегия не запустилась (WinDivert/антивирус?). Откройте «Диагностику».")
        return {}
    best = max(tested.values(), key=lambda s: score(results[s.name]))
    if score(results[best.name])[0] <= base_ok and base_ok < n:
        log("Ни одна стратегия не улучшила доступ — выбор не изменён.")
        return {}
    picks = {}
    for g in set(groups.values()):
        keys = [k for k, v in groups.items() if v == g]
        winner = max(tested.values(), key=lambda s: score({k: results[s.name][k] for k in keys}))
        picks[g] = winner.name
    mixed = strategies.mix("mix", {g: tested[n] for g, n in picks.items()}, best)
    # single runs are noisy: re-test finalists several times and sum successes
    finalists = sorted(tested.values(), key=lambda s: score(results[s.name]), reverse=True)[:3] + [mixed]
    totals = {}
    log("Финальная проверка лучших вариантов…")
    for s in finalists:
        runs = [r for r in (test_strategy(runner, s, targets) for _ in range(ROUNDS)) if r]
        totals[s.name] = (sum(score(r)[0] for r in runs), sum(score(r)[1] for r in runs))
        results[f"final:{s.name}"] = runs
        log(f"  {'комбинация' if s.name == 'mix' else s.name}: {totals[s.name][0]} из {n * ROUNDS}")
    win = max(totals, key=totals.get)
    choice = {"base": best.name, "picks": picks} if win == "mix" else {"base": win, "picks": {}}
    paths.DATA.mkdir(exist_ok=True)
    (paths.DATA / "results.json").write_text(json.dumps(results, indent=1), encoding="utf-8")
    save_settings(strategy=choice)
    log(f"Готово. Выбрано: {'комбинация стратегий' if choice['picks'] else choice['base']}")
    return choice


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
