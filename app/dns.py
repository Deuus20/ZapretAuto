"""Публичные DNS: проверка ответов, подбор лучшего и смена системного DNS.

Ни одному резолверу не верим на слово и не делим их на «хорошие» и «плохие» по имени: ответы сравниваются с
DNS-over-HTTPS, скорость измеряется на месте, а режим белых списков (когда ни один внешний DNS не отвечает)
распознаётся отдельно. Список «рабочих» DNS меняется каждые пару месяцев, поэтому всё измеряется заново.
"""
import ipaddress
import os
import socket
import struct
import time
from concurrent.futures import ThreadPoolExecutor
from statistics import median

import requests

# name, IPv4 servers, DoH endpoint (None: нет публичного DoH)
RESOLVERS = (
    ("Cloudflare", ("1.1.1.1", "1.0.0.1"), "https://cloudflare-dns.com/dns-query"),
    ("Google", ("8.8.8.8", "8.8.4.4"), "https://dns.google/dns-query"),
    ("Quad9", ("9.9.9.9", "149.112.112.112"), "https://dns.quad9.net/dns-query"),
    ("AdGuard", ("94.140.14.14", "94.140.15.15"), "https://dns.adguard-dns.com/dns-query"),
    ("Яндекс", ("77.88.8.8", "77.88.8.1"), None),  # Basic, без фильтрации; ответы всё равно проверяем
)
# Домены для проверки: заблокированные (подмена видна сразу), обычные (работает ли резолвер вообще)
CHECK_DOMAINS = ("discord.com", "www.youtube.com", "ya.ru")
# Discord живёт на anycast Cloudflare: адреса одинаковые у всех, и несовпадение с DoH - верный признак подмены.
# YouTube и Яндекс отдают разные адреса разным резолверам (гео-кэши), сравнивать их множества нельзя.
FORGE_STRICT = ("discord.com",)
UDP_TIMEOUT = 2.0
DOH_TIMEOUT = 5.0
MAX_ANSWER = 32          # больше записей в ответе не разбираем: защита от мусора
MAX_PACKET = 4096
_DOH_HEADERS = {"accept": "application/dns-message", "content-type": "application/dns-message"}


def valid_ip(s: str) -> bool:
    """Только IPv4: IPv6-настройки на Windows 10 не поддерживают системный DoH, и их никто не проверяет."""
    try:
        return ipaddress.ip_address(s.strip()).version == 4
    except ValueError:
        return False


def _encode_name(name: str) -> bytes:
    parts = name.strip(".").split(".")
    if not 1 <= len(parts) <= 32 or any(not p or len(p) > 63 for p in parts):
        raise ValueError(f"bad dns name: {name!r}")
    try:
        body = b"".join(bytes([len(p)]) + p.encode("idna") for p in parts)
    except UnicodeError as e:
        raise ValueError(f"bad dns name: {name!r}") from e
    return body + b"\0"


def query_packet(name: str) -> tuple[bytes, int]:
    """DNS-запрос A-записи; случайный id защищает от подмешанного ответа."""
    qid = int.from_bytes(os.urandom(2), "big")
    header = struct.pack("!HHHHHH", qid, 0x0100, 1, 0, 0, 0)  # RD=1, один вопрос
    return header + _encode_name(name) + struct.pack("!HH", 1, 1), qid


def _skip_name(data: bytes, off: int) -> int | None:
    """Пропустить имя, включая указатели; None на битом пакете."""
    for _ in range(64):  # защита от зацикленных указателей
        if off >= len(data):
            return None
        ln = data[off]
        if ln == 0:
            return off + 1
        if ln & 0xC0 == 0xC0:  # указатель: два байта, дальше всегда конец имени
            return off + 2 if off + 1 < len(data) else None
        if ln & 0xC0:
            return None
        off += 1 + ln
    return None


def parse_answers(data: bytes, qid: int) -> set[str] | None:
    """A-адреса из ответа; None если это не ответ на наш запрос или пакет битый."""
    if len(data) < 12:
        return None
    rid, flags, qd, an = struct.unpack("!HHHH", data[:8])
    if rid != qid or not flags & 0x8000 or (flags & 0xF) != 0 or qd != 1:
        return None
    off = _skip_name(data, 12)
    if off is None or off + 4 > len(data):
        return None
    off += 4  # QTYPE + QCLASS
    out: set[str] = set()
    for _ in range(min(an, MAX_ANSWER)):
        off = _skip_name(data, off)
        if off is None or off + 10 > len(data):
            return None
        rtype, _cls, _ttl, rdlen = struct.unpack("!HHIH", data[off:off + 10])
        off += 10
        if off + rdlen > len(data):
            return None
        if rtype == 1 and rdlen == 4:
            out.add(str(ipaddress.IPv4Address(data[off:off + 4])))
        off += rdlen
    return out


def udp_query(server: str, name: str, timeout: float = UDP_TIMEOUT) -> tuple[set[str] | None, float | None]:
    """Ответ и время в секундах; (None, None) если резолвер молчит или ответ не разобрался."""
    if not valid_ip(server):
        return None, None
    packet, qid = query_packet(name)
    t0 = time.perf_counter()
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.settimeout(timeout)
            s.connect((server, 53))
            s.send(packet)
            data = s.recv(MAX_PACKET)
    except OSError:
        return None, None
    dt = time.perf_counter() - t0
    answers = parse_answers(data, qid)
    return (answers, dt) if answers is not None else (None, None)


def doh_query(url: str, name: str, timeout: float = DOH_TIMEOUT, session: requests.Session | None = None) -> set[str] | None:
    """Ответ через DoH (RFC 8484, бинарный формат): тот же пакет, только по HTTPS."""
    try:
        packet, qid = query_packet(name)
        http = session or requests
        r = http.post(url, data=packet, headers=_DOH_HEADERS, timeout=timeout)
        if r.status_code != 200 or len(r.content) > MAX_PACKET:
            return None
        return parse_answers(r.content, qid)
    except requests.RequestException:  # ValueError из query_packet сюда не попадает: имена наши
        return None


def baseline(names=CHECK_DOMAINS) -> dict[str, set[str]]:
    """Эталонные ответы от первого рабочего DoH. Пусто, если все DoH заблокированы (так бывает)."""
    def one(entry) -> dict[str, set[str]] | None:
        _n, _ips, doh = entry
        if not doh:
            return None
        got: dict[str, set[str]] = {}
        with requests.Session() as sess:  # три запроса к одной точке: одно TLS-соединение вместо трёх
            for n in names:
                answers = doh_query(doh, n, session=sess)
                if not answers:
                    return None
                got[n] = answers
        return got

    with ThreadPoolExecutor(len(RESOLVERS)) as ex:
        for got in ex.map(one, RESOLVERS):
            if got:
                return got
    return {}


def _bogus(ip: str) -> bool:
    """Заглушка вместо адреса: 0.0.0.0, петля, приватная или служебная сеть."""
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return True
    return a.is_private or a.is_loopback or a.is_unspecified or a.is_link_local or a.is_multicast or a.is_reserved


def check(server: str, baseline_answers: dict[str, set[str]]) -> dict:
    """Один резолвер: ответы по трём доменам, подмена, скорость."""
    times, answers, forged = [], {}, []
    for n in CHECK_DOMAINS:
        got, dt = udp_query(server, n)
        if not got:  # молчит или ответ без A-записей: для проверки это отказ
            continue
        times.append(dt)
        answers[n] = got
        ref = baseline_answers.get(n)
        if any(_bogus(ip) for ip in got):
            forged.append(n)
        elif ref and n in FORGE_STRICT and not (got & ref):
            forged.append(n)
    status = "down" if not answers else ("forged" if forged else "ok")
    return {"server": server, "status": status, "ms": round(median(times) * 1000, 1) if times else None,
            "answers": answers, "forged": forged}


def _doh_ok(url: str | None) -> bool:
    return bool(url) and doh_query(url, "ya.ru") is not None


def rank(log=print) -> list[dict]:
    """Проверить популярные резолверы и DNS провайдера; лучшие первыми.

    DNS провайдера участвует наравне: если он быстрый и не врёт, менять ничего не нужно."""
    base = baseline()
    if not base:
        log("DNS: ни один DoH не ответил, ответы сравнить не с чем, выбираю только по скорости")
    current_servers = [s for s in current() if valid_ip(s)]
    candidates: list[tuple[str, tuple[str, ...], str | None]] = [(n, ips, doh) for n, ips, doh in RESOLVERS]
    known = {ip for _n, ips, _d in RESOLVERS for ip in ips}
    provider = [s for s in current_servers if s not in known]  # тот же 1.1.1.1 дважды не проверяем
    for i, s in enumerate(provider):  # DNS провайдера - наравне со всеми
        candidates.append((f"Провайдер{'' if i == 0 else f' {i + 1}'}", (s,), None))

    def one(c):
        name, ips, doh = c
        res = check(ips[0], base)
        res.update({"name": name, "ips": ips, "doh": _doh_ok(doh)})
        return res

    with ThreadPoolExecutor(max(len(candidates), 1)) as ex:
        results = list(ex.map(one, candidates))
    order = {"ok": 0, "forged": 1, "down": 2}
    results.sort(key=lambda r: (order[r["status"]], r["ms"] if r["ms"] is not None else 1e9))
    for r in results:
        log(describe(r))
    return results


def describe(r: dict) -> str:
    tail = "" if r["status"] != "ok" else f", {r['ms']} мс" + (", DoH работает" if r.get("doh") else ", без DoH")
    if r["status"] == "forged":
        return f"{r['name']}: подменяет ответы ({', '.join(r['forged'])})"
    if r["status"] == "down":
        return f"{r['name']}: не отвечает"
    return f"{r['name']}: ответы верные{tail}"


def best(log=print) -> dict | None:
    return next((r for r in rank(log) if r["status"] == "ok"), None)


def adapter() -> str | None:
    """Адаптер с маршрутом по умолчанию: именно ему netsh меняет DNS."""
    from .diag import _ps  # diag тяжёлый, нужен только здесь
    rc, out = _ps("adapter")
    name = out.strip().splitlines()[0].strip() if rc == 0 and out.strip() else ""
    return name if name and len(name) <= 128 and '"' not in name else None


def current(alias: str | None = None) -> list[str]:
    """IPv4-адреса DNS на адаптере (по умолчанию на том, что несёт маршрут по умолчанию).

    Не «первый попавшийся»: при включённом VPN первым в списке часто идёт его адаптер, и чужой DNS
    выглядел бы как провайдерский."""
    from .diag import _ps
    rc, out = _ps("dnsservers")
    if rc != 0:
        return []
    table: dict[str, list[str]] = {}
    for line in out.splitlines():
        if "=" in line:
            name, _, servers = line.partition("=")
            table[name.strip()] = [s.strip() for s in servers.split(",") if valid_ip(s)]
    if alias:
        return table.get(alias, [])
    return table.get(adapter() or "", []) or next((v for v in table.values() if v), [])


def apply(servers: list[str]) -> tuple[bool, str]:
    """Поставить IPv4 DNS на активный адаптер, сбросить кэш и прочитать результат обратно."""
    addrs = [s for s in dict.fromkeys(servers) if valid_ip(s)]
    if not addrs:
        return False, "нет подходящих IPv4-адресов"
    name = adapter()
    if not name:
        return False, "не удалось определить активный сетевой адаптер"
    from .diag import _run
    rc, out = _run(["netsh", "interface", "ipv4", "set", "dnsservers", f"name={name}", "source=static",
                    f"address={addrs[0]}", "register=primary"])
    if rc != 0:
        return False, f"netsh: {out.strip()[:160] or 'код ' + str(rc)}"
    for a in addrs[1:]:
        rc, out = _run(["netsh", "interface", "ipv4", "add", "dnsservers", f"name={name}", f"address={a}",
                        "index=2"])
        if rc != 0:
            return False, f"netsh: {out.strip()[:160] or 'код ' + str(rc)}"
    _run(["ipconfig", "/flushdns"])
    got = current(name)
    if addrs[0] not in got:
        return False, f"не применилось: сейчас {', '.join(got) or 'пусто'}"
    return True, f"{', '.join(addrs)} на адаптере {name}"


def restore() -> tuple[bool, str]:
    """Вернуть DNS от провайдера (DHCP)."""
    name = adapter()
    if not name:
        return False, "не удалось определить активный сетевой адаптер"
    from .diag import _run
    rc, out = _run(["netsh", "interface", "ipv4", "set", "dnsservers", f"name={name}", "source=dhcp"])
    if rc != 0:
        return False, f"netsh: {out.strip()[:160] or 'код ' + str(rc)}"
    _run(["ipconfig", "/flushdns"])
    return True, "DNS снова от провайдера"


def repair(log=print) -> str:
    """Починить DNS: лучший доступный резолвер, DoH в браузерах, адреса в hosts, проверка подмены."""
    from . import diag
    out = []
    best_r = best(log)
    if best_r and best_r["name"].startswith("Провайдер"):
        out.append("DNS провайдера отвечает верно и быстрее всех: оставляю как есть")
    elif best_r:
        ok, msg = apply(list(best_r["ips"]))
        out.append(("DNS поставлен: " + msg) if ok else ("DNS не применён: " + msg))
    else:
        out.append("Внешний DNS недоступен: похоже на белые списки, системный DNS не меняю")
    out.append(diag.apply_fix("browser_doh"))
    out.append(diag.apply_fix("hosts_update"))
    bad = diag.dns_poisoned()
    out.append("Подмена DNS осталась (" + ", ".join(bad) + "), нужен VPN" if bad else
               "Подмены DNS больше нет" if bad == [] else
               "Проверить подмену не удалось (нет доступа к DoH), проверьте доступ вручную")
    return "\n".join(out)
