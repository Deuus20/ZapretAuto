"""Experimental zapret2 mode: winws2 rotates strategies per site by itself (Lua 'circular' orchestrator).

When a site's connections start failing (RST from DPI, retransmissions), winws2 switches that site to the next
strategy on the fly: no restart and no 2–4 min re-pick. Strategies are ports of Flowseal's favourites.
Files come one by one from bol-van/zapret-win-bundle at a pinned commit and are checked by sha256: the zapret2
release zip itself is flagged by Windows Defender's archive reputation check (Trojan:Win32/Suschil!rfn,
a false positive: every file inside scans clean).
"""
import hashlib
from pathlib import Path

import requests

from . import paths

COMMIT = "6eb463a6758fb48cd101bc55dfd057e6e9d98af1"
BASE = f"https://raw.githubusercontent.com/bol-van/zapret-win-bundle/{COMMIT}/zapret-winws/"
FILES = {
    "winws2.exe": "eb9807972c15f05a00416b0549d766a71c9186f8101d406e3cc6c16278164d9e",
    "WinDivert.dll": "16abd6a029e65557c6a309bea7b13bf81fff4e193567582e1cddbf6719f323e0",
    "WinDivert64.sys": "8da085332782708d8767bcace5327a6ec7283c17cfb85e40b03cd2323a90ddc2",
    "cygwin1.dll": "103104a52e5293ce418944725df19e2bf81ad9269b9a120d71d39028e821499b",
    "lua/zapret-lib.lua": "cb21ddedd08fa78f87c091de33bdd7e5c0bd24adfff4868078f77197c5992137",
    "lua/zapret-antidpi.lua": "31c9dd75b0bd55e98e5306293f2be81e9d2ecadcbbf9157394ff37dcff7dc85a",
    "lua/zapret-auto.lua": "c2028f544134b0dec33f054ac54fe4f2315d969ba034601456f72017fd5f8528",
    "windivert.filter/windivert_part.discord_media.txt":
        "2f2deaf49ed68eb684258497134181753188f2b475161deb244488a53872b7c5",
    "windivert.filter/windivert_part.stun.txt": "2a14f470b00cd9704003e0cade500086398f9222c858d9ae944bdeb89f520595",
    "windivert.filter/windivert_part.quic_initial_ietf.txt":
        "f243ddc22758b9e04838211a3815e43fb3abe615e6be29e6a812ae4c72b392e0",
}
NAME = "zapret2 (эксперимент)"
GAME_OFF = "12"  # same trick as Flowseal: a port nothing uses when the game filter is off

# strategies circular rotates through for TLS/HTTP (ports of Flowseal favourites + the bundle's example)
BADSEQ = "tcp_seq=2:tcp_ack=-66000:tcp_ts_up"  # nfqws1 --dpi-desync-fooling=badseq --dpi-desync-badseq-increment=2
TTL = "ip_autottl=-2,3-20:ip6_autottl=-2,3-20"
ROTATION = [
    # 1. ALT8 / SIMPLE FAKE: fakes with bad seq
    [("tls_client_hello", f"fake:blob=fake_default_tls:repeats=6:{BADSEQ}"),
     ("http_req", f"fake:blob=fake_default_http:repeats=6:{BADSEQ}")],
    # 2. md5sig fakes + disorder at the middle of the domain
    [("tls_client_hello", "fake:blob=fake_default_tls:tcp_md5:tcp_seq=-10000:repeats=6"),
     ("tls_client_hello", "multidisorder:pos=midsld"),
     ("http_req", "fake:blob=fake_default_http:tcp_md5:repeats=6"),
     ("http_req", "multisplit:pos=method+2")],
    # 3. ALT: fake + fakedsplit with timestamp fooling
    [("tls_client_hello", "fake:blob=tls_google:tcp_ts=-600000:repeats=6"),
     ("tls_client_hello", "fakedsplit:pos=1:pattern=0x00:tcp_ts=-600000"),
     ("http_req", "fake:blob=fake_default_http:tcp_ts=-600000:repeats=6"),
     ("http_req", "fakedsplit:pos=method+2:pattern=0x00:tcp_ts=-600000")],
    # 4. seqovl split: no fakes at all (helps where fakes are filtered)
    [("tls_client_hello", "multisplit:pos=1:seqovl=681:seqovl_pattern=tls_google"),
     ("http_req", "multisplit:pos=method+2:seqovl=2")],
    # 5. low-TTL fakes (auto TTL)
    [("tls_client_hello", f"fake:blob=fake_default_tls:repeats=6:{TTL}"),
     ("tls_client_hello", f"fakedsplit:pos=1:{TTL}"),
     ("http_req", f"fake:blob=fake_default_http:repeats=6:{TTL}"),
     ("http_req", f"fakedsplit:pos=method+2:{TTL}")],
]


def root() -> Path:
    return paths.DATA / "zapret2"


def _ok(p: Path, sha: str) -> bool:
    try:
        return hashlib.sha256(p.read_bytes()).hexdigest() == sha
    except OSError:
        return False


_cache: tuple | None = None  # (stat key, result): the GUI asks every 2 s, hashing 4 MB each time is waste


def installed() -> bool:
    global _cache
    key = []
    for name in FILES:
        try:
            st = (root() / name).stat()
            key.append((name, st.st_size, st.st_mtime_ns))
        except OSError:
            return False
    key = (str(root()), tuple(key))
    if _cache is None or _cache[0] != key:
        _cache = (key, all(_ok(root() / name, sha) for name, sha in FILES.items()))
    return _cache[1]


def _blob(name: str, file: str, fallback: str) -> str:
    """Flowseal's fake from vendor/bin; a built-in one if an update renamed/removed it (winws2 won't start else)."""
    p = paths.BIN / file
    return f"--blob={name}:@{p}" if p.is_file() else f"--blob={name}:{fallback}"


def install(log=print) -> bool:
    """Download missing/changed files; each must match its pinned sha256 (else nothing is replaced)."""
    for name, sha in FILES.items():
        dst = root() / name
        if _ok(dst, sha):
            continue
        log(f"zapret2: скачиваю {name}")
        try:
            data = requests.get(BASE + name, timeout=60).content
        except requests.RequestException as e:
            log(f"zapret2: не удалось скачать {name}: {e}")
            return False
        if hashlib.sha256(data).hexdigest() != sha:
            log(f"zapret2: {name} не совпал по контрольной сумме — не устанавливаю")
            return False
        dst.parent.mkdir(parents=True, exist_ok=True)
        tmp = dst.with_name(dst.name + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(dst)
    return True


def _circular() -> list[str]:
    """Per-site rotation: count failures, switch the site to the next strategy at 3 within a minute."""
    out = ["--out-range=-s34228", "--in-range=-s5556", "--lua-desync=circular:fails=3", "--in-range=x"]
    for n, steps in enumerate(ROTATION, 1):
        for payload, call in steps:
            out += [f"--payload={payload}", f"--lua-desync={call}:strategy={n}"]
    return out


class Strategy2:
    """Looks like strategies.Strategy to the runner/GUI: a name and argv()."""
    name = NAME
    profiles: list = []

    def argv(self, game_tcp: bool = False, game_udp: bool = False, auto_hostlist: bool = False) -> list[str]:
        r, ls = root(), paths.LISTS
        gt = "1024-65535" if game_tcp else GAME_OFF
        gu = "1024-65535" if game_udp else GAME_OFF
        hosts = [f"--hostlist={ls / 'list-general.txt'}", f"--hostlist={ls / 'list-general-user.txt'}",
                 f"--hostlist={ls / 'list-google.txt'}"]
        if auto_hostlist:
            hosts += [f"--hostlist-auto={ls / 'list-auto.txt'}", "--hostlist-auto-fail-threshold=3"]
        excl = [f"--hostlist-exclude={ls / 'list-exclude.txt'}", f"--hostlist-exclude={ls / 'list-exclude-user.txt'}",
                f"--ipset-exclude={ls / 'ipset-exclude.txt'}", f"--ipset-exclude={ls / 'ipset-exclude-user.txt'}",
                f"--ipset-exclude={ls / 'ipset-exclude-vpn.txt'}"]
        ipsets = [f"--ipset={ls / 'ipset-all.txt'}", f"--ipset={ls / 'ipset-games.txt'}",
                  f"--ipset={ls / 'ipset-telegram.txt'}"]
        no_game_web = [f"--ipset-exclude={ls / 'ipset-exclude-games.txt'}"]
        return [
            str(r / "winws2.exe"),
            f"--wf-tcp-out=80,443,2053,2083,2087,2096,8443,{gt}", f"--wf-udp-out=443,{gu}",
            f"--wf-raw-part=@{r / 'windivert.filter' / 'windivert_part.discord_media.txt'}",
            f"--wf-raw-part=@{r / 'windivert.filter' / 'windivert_part.stun.txt'}",
            f"--lua-init=@{r / 'lua' / 'zapret-lib.lua'}", f"--lua-init=@{r / 'lua' / 'zapret-antidpi.lua'}",
            f"--lua-init=@{r / 'lua' / 'zapret-auto.lua'}",
            *[_blob(n, f, "0x" + "00" * 64) for n, f in (
                ("tls_google", "tls_clienthello_www_google_com.bin"), ("quic_google", "quic_initial_www_google_com.bin"),
                ("discord_udp", "ACTIVE_DISCORD_UDP.bin"), ("game_udp", "ACTIVE_GAME_UDP.bin"))],
            # sites from the lists (Discord, YouTube...), rotating strategies per site
            "--filter-tcp=80,443", "--filter-l7=tls,http", *hosts, *excl, *_circular(), "--new",
            "--filter-tcp=2053,2083,2087,2096,8443", "--hostlist-domains=discord.media", *_circular(), "--new",
            "--filter-tcp=80,443,8443", "--filter-l7=tls,http", *ipsets, *excl, *no_game_web, *_circular(), "--new",
            # QUIC (HTTP/3) and Discord voice: fixed fakes, as in ALT8
            "--filter-udp=443", "--filter-l7=quic", *hosts, *excl, "--payload=quic_initial",
            "--lua-desync=fake:blob=quic_google:repeats=6", "--new",
            "--filter-udp=443", "--filter-l7=quic", *ipsets, *excl, *no_game_web, "--payload=quic_initial",
            "--lua-desync=fake:blob=quic_google:repeats=6", "--new",
            "--filter-l7=discord,stun", "--payload=stun,discord_ip_discovery",
            "--lua-desync=fake:blob=discord_udp:repeats=6", "--new",
            # games: first packets of every flow to game servers (ipset), as Flowseal's game filter
            f"--filter-tcp={gt}", *ipsets, *excl, "--out-range=-n3",
            f"--lua-desync=fake:blob=tls_google:payload=~empty:repeats=6:{BADSEQ}", "--new",
            f"--filter-udp={gu}", *ipsets, *excl, "--out-range=-n2",
            "--lua-desync=fake:blob=game_udp:payload=~empty:repeats=10",
        ]
