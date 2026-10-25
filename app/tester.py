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
KEEP_MARGIN = 2  # finals: a new strategy must beat the current one by MORE than this many passed probes
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

    @property
    def is_speed(self) -> bool:
        return self.url.startswith("speed://")


from .vpn import VPN_RE  # noqa: E402

BIND: str | None = None  # source IP of a physical adapter when testing around a full-tunnel VPN


class _SrcAdapter(requests.adapters.HTTPAdapter):
    def __init__(self, src: str):
        self.src = src
        super().__init__()

    def init_poolmanager(self, *a, **kw):
        kw["source_address"] = (self.src, 0)
        return super().init_poolmanager(*a, **kw)


class _Enough(Exception):
    """Raised from the curl write callback once enough bytes arrived."""


def fetch(url: str, max_bytes: int, deadline: float, h3: bool = False) -> tuple[int, str, int]:
    """Download up to max_bytes; returns (status, final_url, bytes). Uses a Chrome TLS fingerprint
    (curl_cffi) when available: DPI and strategies react to the ClientHello, and browsers/Discord send
    Chrome's, not Python's. Raises on network failure."""
    t0 = time.perf_counter()
    try:
        from curl_cffi import requests as cr
    except ImportError:
        cr = None
    if cr is not None:
        # Non-streaming request with callbacks: curl_cffi's stream=True leaked a thread + handles per request
        # (added up in the 24/7 health checks); a raw Curl handle lost curl_cffi's request setup and fared worse
        # against DPI. Raising from the content callback is the only way to stop a transfer early.
        # An early stop means a big real page (status 200, original URL); ISP stub pages (451 / redirect to a
        # warning site) are small, finish normally and keep their real status and final URL.
        got, status, final = 0, 200, url

        def on_data(data: bytes) -> None:
            nonlocal got
            got += len(data)
            if got >= max_bytes or time.perf_counter() - t0 > deadline:
                raise _Enough

        kw = {"interface": BIND} if BIND else {}
        if h3:  # QUIC (UDP 443): browsers use it for YouTube/Google, and strategies have separate UDP profiles
            kw["http_version"] = "v3only"
        with cr.Session(impersonate="chrome") as sess:
            try:
                r = sess.get(url, timeout=(TIMEOUT, TIMEOUT + deadline), proxies={}, content_callback=on_data, **kw)
                status, final = r.status_code, str(r.url)
            except _Enough:
                pass
            except Exception:  # noqa: BLE001
                if got == 0:
                    raise
        return status, final, got
    with _get(url, timeout=TIMEOUT, stream=True, proxies={"http": None, "https": None},
              headers={"User-Agent": "Mozilla/5.0"}) as r:
        got = 0
        for chunk in r.iter_content(1 << 14):
            got += len(chunk)
            if got >= max_bytes or time.perf_counter() - t0 > deadline:
                break
        return r.status_code, r.url, got


def _get(url: str, **kw):
    """requests.get that leaves through BIND's adapter when set (Windows strong-host routing)."""
    s = requests.Session()
    if BIND:
        s.mount("https://", _SrcAdapter(BIND))
        s.mount("http://", _SrcAdapter(BIND))
    return s.get(url, **kw)


def egress_ip() -> str | None:
    """Local IPv4 used for internet traffic (cheap: one UDP socket, no packets sent). Changes when the network does."""
    import socket
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 53))
            return s.getsockname()[0]
    except OSError:
        return None


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
    # collateral checks: hosts in ipset ranges (Fastly) that work without bypass. A strategy that breaks them
    # (e.g. game launchers on Fastly/Cloudflare) loses points instead of silently winning the ipset group.
    out += [Target("FastlyPyPI", "https://pypi.org"), Target("FastlyPython", "https://www.python.org"),
            Target("GitHub", "https://github.com")]  # also needed for our own updates
    out += STUN_TARGETS
    out += _quic_targets()
    out.append(Target("YouTubeSpeed", "speed://youtube", "tcp:443:list-google"))
    out.append(Target("YouTubeVideo", "ytcdn://", "tcp:443:list-google"))  # the video cache server itself
    if load_settings().get("telegram"):
        out.append(Target("TelegramWeb", "https://web.telegram.org"))
        out.append(Target("TelegramDC2", "tcp://149.154.167.50:443"))
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
                if BIND:
                    s.bind((BIND, 0))
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
    if not p.exists():  # before the first sync_lists(): fall back to the upstream copy
        p = paths.VENDOR / "lists" / name
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
    if load_settings().get("auto_hostlist", True):
        # --hostlist-auto makes the general profile take every 80/443 host it sees (to learn blocked ones),
        # so ipset hosts never reach the ipset profile: score them for the general profile
        return "tcp:80,443:list-general"
    return "tcp:80,443,8443:ipset-all"


BLOCK_PAGE = re.compile(r"warning\.rt\.ru|blocked|zapret-info|rkn\.gov|eais", re.I)


SPEED_MIN = 250 * 1024    # B/s below which YouTube counts as throttled
SPEED_BYTES = 1536 * 1024


def probe_speed(t: Target) -> float | None:
    """Download the ~1 MB YouTube front page completely: catches DPI that freezes a session after the
    first kilobytes. Seconds taken, None if it stalls or is slower than SPEED_MIN."""
    t0 = time.perf_counter()
    try:
        _, _, got = fetch("https://www.youtube.com/", SPEED_BYTES, 10)
    except Exception:  # noqa: BLE001
        return None
    dt = time.perf_counter() - t0
    return dt if got >= 400 * 1024 and got / dt >= SPEED_MIN else None


_CDN_NODE: str | None = None  # this network's YouTube video cache, e.g. "comstar-bka2" (asked once)


def probe_video_cdn() -> float | None:
    """The server videos actually stream from (rrN---<node>.googlevideo.com, often a cache at the ISP): ISPs
    slow YouTube down on these hosts, not on www.youtube.com. Seconds for /generate_204, None on failure."""
    global _CDN_NODE
    t0 = time.perf_counter()
    try:
        if not _CDN_NODE:
            from curl_cffi import requests as cr
            kw = {"interface": BIND} if BIND else {}
            text = cr.get("https://redirector.googlevideo.com/report_mapping?di=no", impersonate="chrome",
                          timeout=TIMEOUT * 2, **kw).text  # "<your ip> => node (net)": the IP is not kept
            m = re.search(r"=>\s*([a-z0-9-]+)", text)
            if not m:
                return None
            _CDN_NODE = m.group(1)
        status, _, _ = fetch(f"https://rr1---{_CDN_NODE}.googlevideo.com/generate_204", 1024, TIMEOUT * 2)
    except Exception:  # noqa: BLE001
        _CDN_NODE = None  # another network may map to another cache: ask again next time
        return None
    return time.perf_counter() - t0 if status == 204 else None


def probe_tcp(t: Target) -> float | None:
    import socket
    host, port = t.url[6:].rsplit(":", 1)
    t0 = time.perf_counter()
    try:
        with socket.create_connection((host, int(port)), timeout=TIMEOUT,
                                      source_address=(BIND, 0) if BIND else None):
            return time.perf_counter() - t0
    except OSError:
        return None


def _quic_targets() -> list["Target"]:
    """HTTP/3 probes, scored for the UDP:443 profile that handles the host (hostlist vs ipset)."""
    try:
        import importlib.util
        if not importlib.util.find_spec("curl_cffi"):
            return []
    except Exception:  # noqa: BLE001
        return []
    gen = _domains("list-general.txt") | _domains("list-general-user.txt")
    out = []
    for key, host in (("YouTubeQUIC", "www.youtube.com"), ("DiscordQUIC", "discord.com")):
        listed = any(host == d or host.endswith("." + d) for d in gen)
        out.append(Target(key, f"h3://{host}/", "udp:443:list-general" if listed else "udp:443:ipset-all"))
    return out


def probe(t: Target) -> float | None:
    """Seconds to read READ_BYTES (or full body), None on failure."""
    if t.is_stun:
        return probe_stun(t)
    if t.url == "ytcdn://":
        return probe_video_cdn()
    if t.is_speed:
        return probe_speed(t)
    if t.url.startswith("tcp://"):
        return probe_tcp(t)
    t0 = time.perf_counter()
    h3 = t.url.startswith("h3://")
    url = "https://" + t.url[5:] if h3 else t.url
    try:
        status, final, _ = fetch(url, READ_BYTES, TIMEOUT * 2, h3=h3)
    except Exception:  # noqa: BLE001  (requests / curl_cffi network errors)
        return None
    if status == 451 or BLOCK_PAGE.search(final):  # ISP stub page
        return None
    return time.perf_counter() - t0


def probe_all(targets: list[Target]) -> dict[str, float | None]:
    with ThreadPoolExecutor(max(1, min(len(targets), 24))) as ex:  # all at once: wall time = slowest probe
        return dict(zip((t.key for t in targets), ex.map(probe, targets)))


def score(res: dict[str, float | None]) -> tuple[int, float]:
    ok = [v for v in res.values() if v is not None]
    return len(ok), -(sum(ok) / len(ok) if ok else 99)


def test_strategy(runner: Runner, s: strategies.Strategy, targets, warmup: float = WARMUP) -> dict | None:
    try:
        runner.start(s, auto_hostlist=load_settings().get("auto_hostlist", True))  # test what will run
        time.sleep(warmup)
        if not runner.running:
            return None
        res = probe_all(targets)
        return res if runner.running else None  # winws died during probing
    finally:
        runner.stop()


# service -> probes; a service counts as working when most of its probes pass (single probes through DPI are noisy)
QUICK = {"Discord": ("DiscordMain", "DiscordGateway", "DiscordCDN"),
         "YouTube": ("YouTubeWeb", "YouTubeImage", "YouTubeVideoRedirect", "YouTubeVideo"),
         "Скорость YouTube": ("YouTubeSpeed",), "Голос Discord": ("STUN_Google19302",), "Telegram": ("TelegramWeb",)}


def quick_check(light: bool = False) -> dict[str, bool]:
    """Check key services through the currently running bypass (does not touch winws).
    light=True skips the ~1 MB YouTube speed test (periodic background checks: ~144 MB/day otherwise)."""
    global BIND
    from . import vpn as vpnmod
    BIND = vpnmod.bind_ip() if vpn_active() else None  # check the direct path, not the VPN
    if BIND is None and vpn_active():
        return {}  # full tunnel with a kill switch: the bypass isn't on the path, failures would be false alarms
    keys ={k for ks in QUICK.values() for k in ks}
    ts = [t for t in load_targets() if t.key in keys and not (light and t.is_speed)]
    res = probe_all(ts)
    retry = [t for t in ts if res.get(t.key) is None]
    if retry:  # single probes through DPI are noisy: confirm failures once
        time.sleep(3)
        res.update({k: v for k, v in probe_all(retry).items() if v is not None})
    out = {}
    for name, ks in QUICK.items():
        got = [res[k] is not None for k in ks if k in res]
        if got:
            out[name] = sum(got) * 2 > len(got) or (len(got) == 1 and got[0])
    return out


def auto_pick(log=print, use_shared: bool | None = None, stop=lambda: False) -> dict:
    """Test every strategy, mix the best profile per group, save to data/settings.json."""
    runner, targets = Runner(), load_targets()
    paths.sync_lists()
    all_s = strategies.load_all()
    groups = {t.key: target_group(t) for t in targets}

    global BIND
    BIND = None
    if vpn := vpn_active():
        from . import vpn as vpnmod
        BIND = vpnmod.bind_ip()
        if not BIND:
            log(f"Включён VPN «{vpn}», а обойти его для проверки не удалось. "
                "Отключите VPN или настройте раздельное туннелирование.")
            return {}
        log(f"Включён VPN «{vpn}» — проверяю мимо него, через адаптер {BIND}.")
    if use_shared is None:  # shared picks are a fast first setup; re-picks always search fully
        use_shared = not load_settings().get("strategy")
    shared = _try_shared(runner, targets, log) if use_shared else None
    if shared:
        return shared
    try:
        from .diag import dns_poisoned
        if forged := dns_poisoned():
            log("Внимание: провайдер подменяет DNS (" + ", ".join(forged) + "). Пока не включён защищённый DNS "
                "(DoH), обход не поможет — см. «Диагностику».")
    except Exception:  # noqa: BLE001
        pass
    results = {"_baseline": probe_all(targets)}
    n = len(targets)
    base_ok = score(results["_baseline"])[0]
    log(f"Без обхода доступно: {base_ok} из {n}")
    if base_ok == 0:
        log("Похоже, интернета нет — подбор отменён.")
        return {}
    current = load_settings().get("strategy")
    if current and base_ok >= n - 2:
        # DPI is barely active right now (often at night): every strategy passes, so a pick would be noise
        log(f"Сейчас почти ничего не блокируется ({base_ok} из {n} без обхода) — сравнивать стратегии не на чем. "
            "Текущая стратегия сохранена; подберите заново днём, если что-то перестанет работать.")
        return current
    for i, s in enumerate(all_s, 1):
        if stop():
            log("Подбор отменён.")
            return {}
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
        w_ok = score({k: results[winner.name][k] for k in keys})[0]
        b_ok = score({k: results[best.name][k] for k in keys})[0]
        if w_ok > b_ok:  # single runs are noisy: swap a profile only for a real gain on that group
            picks[g] = winner.name
    mixed = strategies.mix("mix", {g: tested[n] for g, n in picks.items()}, best)
    # single runs are noisy: re-test finalists several times and sum successes
    finalists = sorted(tested.values(), key=lambda s: score(results[s.name]), reverse=True)[:3] + [mixed]
    cur_s = None
    if current:  # the working strategy competes too: switching has a cost, so it must be clearly beaten
        by = {s.name: s for s in all_s}
        try:
            cur_s = (strategies.mix("current", {g: by[x] for g, x in current["picks"].items()}, by[current["base"]])
                     if current.get("picks") else strategies.mix("current", {}, by[current["base"]]))
            finalists.append(cur_s)
        except KeyError:  # strategy vanished after a zapret update
            cur_s = None
    totals = {}
    log("Финальная проверка лучших вариантов…")
    for s in finalists:
        if stop():
            log("Подбор отменён.")
            return {}
        runs = [r for r in (test_strategy(runner, s, targets) for _ in range(ROUNDS)) if r]
        totals[s.name] = (sum(score(r)[0] for r in runs), sum(score(r)[1] for r in runs))
        results[f"final:{s.name}"] = runs
        label = {"mix": "комбинация", "current": "текущая"}.get(s.name, s.name)
        log(f"  {label}: {totals[s.name][0]} из {n * ROUNDS}")
    win = max(totals, key=totals.get)
    if cur_s and win != "current" and totals[win][0] - totals["current"][0] <= KEEP_MARGIN:
        log(f"Новые варианты не лучше текущей стратегии заметно (не больше чем на {KEEP_MARGIN}) — оставляю текущую.")
        win = "current"
    if win == "current":
        choice = current
    else:
        choice = {"base": best.name, "picks": picks} if win == "mix" else {"base": win, "picks": {}}
    paths.DATA.mkdir(exist_ok=True)
    (paths.DATA / "results.json").write_text(json.dumps(results, indent=1), encoding="utf-8")
    save_settings(strategy=choice, auto_choice=choice,  # auto_choice: what «Авто» in the GUI switches back to
                  last_pick={"score": totals[win][0], "total": len(targets) * ROUNDS, "time": time.time(),
                             "before": totals["current"][0] if cur_s else None,
                             "services": _services(results[f"final:{win}"])})
    remember_network(choice)
    log(f"Готово. Выбрано: {'комбинация стратегий' if choice['picks'] else choice['base']}")
    return choice


def remember_network(choice: dict, who: tuple[str, str] | None = None) -> None:
    """Remember the pick for this ISP (ASN): back on that network, it is switched on at once, without a pick.
    Not behind a VPN: the ASN would be the VPN's."""
    if not choice or vpn_active():
        return
    if who is None:
        from . import sharedb
        who = sharedb.isp()
    if not who:
        return
    nets = dict(load_settings().get("networks") or {})
    nets[who[0]] = {"isp": who[1], "choice": choice, "time": time.time()}
    save_settings(networks=nets, net_asn=who[0])


def _services(runs: list[dict]) -> dict[str, bool]:
    """Per service (QUICK): passed in most of these runs — the status chips right after a pick."""
    out = {}
    for name, ks in QUICK.items():
        got = [r[k] is not None for r in runs for k in ks if k in r]
        if got:
            out[name] = sum(got) * 2 > len(got)
    return out


def _try_shared(runner: Runner, targets, log) -> dict | None:
    """Use a combination proven by other users of the same ISP if it passes here too (~30 s)."""
    from . import sharedb, updater
    try:
        who = sharedb.isp()
        if not who:
            return None
        ver = updater.current_version()
        c = sharedb.suggestion(who[0], ver)
        if not c:
            log(f"Провайдер {who[1]} ({who[0]}): в общей базе пока нет данных — полный подбор.")
            return None
        by = {s.name: s for s in strategies.load_all()}
        if c["base"] not in by or any(n not in by for n in c.get("picks", {}).values()):
            return None
        s = strategies.mix("mix", {g: by[n] for g, n in c["picks"].items()}, by[c["base"]]) if c.get("picks") \
            else by[c["base"]]
        log(f"Провайдер {who[1]}: проверяю комбинацию из общей базы…")
        runs = [r for r in (test_strategy(runner, s, targets) for _ in range(ROUNDS)) if r]
        ok = sum(score(r)[0] for r in runs)
        total = len(targets) * ROUNDS
        log(f"  результат: {ok} из {total}")
        if ok >= 0.92 * total:
            save_settings(strategy=c, auto_choice=c, last_pick={"score": ok, "total": total, "shared": True,
                                                                "time": time.time(), "services": _services(runs)})
            remember_network(c, who)
            log("Готово: взята проверенная комбинация для вашего провайдера.")
            return c
        log("Комбинация из базы здесь работает хуже — запускаю полный подбор.")
    except Exception as e:  # noqa: BLE001
        log(f"общая база недоступна: {e}")
    return None


def chosen_strategy() -> strategies.Strategy | None:
    if load_settings().get("zapret2"):  # experimental mode: winws2 rotates strategies per site by itself
        from . import zapret2
        if zapret2.installed():
            return zapret2.Strategy2()
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
