"""Проверка DNS: пакеты, вердикты, подбор и применение (сеть и netsh подменяются)."""
import struct

import pytest

from app import dns


def _response(qid: int, name: str, ips: tuple[str, ...], flags: int = 0x8180) -> bytes:
    """Собрать DNS-ответ вручную: тесты не должны зависеть от сети."""
    q = dns.query_packet(name)[0][12:]  # вопрос как в запросе
    ans = b""
    for ip in ips:
        ans += b"\xc0\x0c" + struct.pack("!HHIH", 1, 1, 60, 4) + bytes(int(x) for x in ip.split("."))
    return struct.pack("!HHHHHH", qid, flags, 1, len(ips), 0, 0) + q + ans


def test_query_packet_round_trip():
    packet, qid = dns.query_packet("discord.com")
    assert packet[:2] == struct.pack("!H", qid) and packet[2:4] == b"\x01\x00"
    resp = _response(qid, "discord.com", ("1.2.3.4", "5.6.7.8"))
    assert dns.parse_answers(resp, qid) == {"1.2.3.4", "5.6.7.8"}


@pytest.mark.parametrize("mutate", [
    lambda r: r[:2] + struct.pack("!H", 0x0100) + r[4:],   # QR=0: это не ответ
    lambda r: r[:5],                                       # обрезанный заголовок
    lambda r: r[:4] + struct.pack("!H", 0x8183) + r[8:],   # RCODE=3 (NXDOMAIN)
])
def test_parse_rejects_bad_packets(mutate):
    _packet, qid = dns.query_packet("ya.ru")
    resp = _response(qid, "ya.ru", ("9.9.9.9",))
    assert dns.parse_answers(mutate(resp), qid) is None


def test_parse_rejects_wrong_id_and_truncated_answer():
    _packet, qid = dns.query_packet("ya.ru")
    resp = _response(qid, "ya.ru", ("9.9.9.9",))
    assert dns.parse_answers(resp, (qid + 1) % 65536) is None
    assert dns.parse_answers(resp[:-2], qid) is None


def test_parse_ignores_non_a_records():
    _packet, qid = dns.query_packet("ya.ru")
    head = bytearray(_response(qid, "ya.ru", ()))
    ptr = bytes(head) + b"\xc0\x0c" + struct.pack("!HHIH", 12, 1, 60, 4) + b"abcd"  # PTR вместо A
    ptr = ptr[:6] + struct.pack("!H", 1) + ptr[8:]  # ANCOUNT=1
    assert dns.parse_answers(ptr, qid) == set()


def test_valid_ip():
    assert dns.valid_ip("1.1.1.1") and not dns.valid_ip("1.1.1.999") and not dns.valid_ip("::1")
    assert not dns.valid_ip("")


def test_check_flags_forgery(monkeypatch):
    base = {"discord.com": {"1.1.1.1"}, "www.youtube.com": {"2.2.2.2"}, "ya.ru": {"3.3.3.3"}}
    answers = {"discord.com": {"4.4.4.4"}}  # anycast: несовпадение = подмена
    monkeypatch.setattr(dns, "udp_query",
                        lambda server, name, **k: (answers.get(name), 0.01) if name in answers else (None, None))
    r = dns.check("1.2.3.4", base)
    assert r["status"] == "forged" and r["forged"] == ["discord.com"]
    assert dns.check("1.2.3.4", {})["status"] == "ok"  # сравнивать не с чем: только скорость


def test_check_does_not_call_geo_answers_forgery(monkeypatch):
    base = {"discord.com": {"1.1.1.1"}, "www.youtube.com": {"2.2.2.2"}, "ya.ru": {"3.3.3.3"}}
    answers = {"discord.com": {"1.1.1.1"}, "www.youtube.com": {"5.5.5.5"}, "ya.ru": {"6.6.6.6"}}
    monkeypatch.setattr(dns, "udp_query",
                        lambda server, name, **k: (answers.get(name), 0.01) if name in answers else (None, None))
    assert dns.check("1.2.3.4", base)["status"] == "ok"  # другой гео-кэш, не подмена


def test_check_flags_stub_ip(monkeypatch):
    answers = {"discord.com": {"0.0.0.0"}, "www.youtube.com": {"127.0.0.1"}, "ya.ru": {"10.0.0.1"}}
    monkeypatch.setattr(dns, "udp_query",
                        lambda server, name, **k: (answers.get(name), 0.01) if name in answers else (None, None))
    r = dns.check("1.2.3.4", {})
    assert r["status"] == "forged" and set(r["forged"]) == set(dns.CHECK_DOMAINS)


def test_rank_puts_working_first_and_includes_provider(monkeypatch):
    monkeypatch.setattr(dns, "baseline", lambda: {})
    monkeypatch.setattr(dns, "current", lambda: ["10.0.0.1"])
    statuses = {"1.1.1.1": "down", "8.8.8.8": "ok", "9.9.9.9": "forged", "94.140.14.14": "ok",
                "77.88.8.8": "ok", "10.0.0.1": "ok"}
    def check(server, base):
        st = statuses[server]
        return {"server": server, "status": st, "ms": 10.0 if st == "ok" else None, "answers": {},
                "forged": ["x"] if st == "forged" else []}
    monkeypatch.setattr(dns, "check", check)
    monkeypatch.setattr(dns, "_doh_ok", lambda url: url is not None)
    out = dns.rank(lambda m: None)
    assert out[0]["status"] == "ok" and out[-1]["status"] == "down"
    assert {r["name"] for r in out} >= {"Cloudflare", "Google", "Провайдер"}
    assert dns.best(lambda m: None)["status"] == "ok"


def test_apply_sets_dns_and_verifies(monkeypatch):
    cmds, state = [], {"current": []}
    monkeypatch.setattr(dns, "adapter", lambda: "Ethernet")
    monkeypatch.setattr(dns, "current", lambda alias=None: list(state["current"]))
    def run(args, timeout=20):
        cmds.append(args)
        if args[:5] == ["netsh", "interface", "ipv4", "set", "dnsservers"]:
            state["current"] = [args[7].split("=", 1)[1]]
        return 0, ""
    monkeypatch.setattr("app.diag._run", run)
    ok, msg = dns.apply(["1.1.1.1", "1.0.0.1"])
    assert ok and "1.1.1.1" in msg
    assert ["ipconfig", "/flushdns"] in cmds
    add = [c for c in cmds if c[:5] == ["netsh", "interface", "ipv4", "add", "dnsservers"]]
    assert add and add[0][6] == "address=1.0.0.1"


def test_apply_rejects_bad_input_and_not_applied(monkeypatch):
    monkeypatch.setattr(dns, "adapter", lambda: "Ethernet")
    assert not dns.apply(["::1", "not-ip"])[0]
    monkeypatch.setattr("app.diag._run", lambda args, timeout=20: (0, ""))
    monkeypatch.setattr(dns, "current", lambda alias=None: [])  # netsh сказал ок, но адреса нет
    ok, msg = dns.apply(["1.1.1.1"])
    assert not ok and "не применилось" in msg
    monkeypatch.setattr(dns, "adapter", lambda: None)
    assert not dns.apply(["1.1.1.1"])[0]


def test_restore_uses_dhcp(monkeypatch):
    cmds = []
    monkeypatch.setattr(dns, "adapter", lambda: "Wi-Fi")
    monkeypatch.setattr("app.diag._run", lambda args, timeout=20: (cmds.append(args), (0, ""))[1])
    ok, _msg = dns.restore()
    assert ok and any("source=dhcp" in c for c in cmds)


def test_repair_reports_after_fix(monkeypatch):
    from app import diag
    calls = []
    monkeypatch.setattr(dns, "best", lambda log=print: {"name": "Cloudflare", "ips": ("1.1.1.1", "1.0.0.1"),
                                                        "status": "ok"})
    monkeypatch.setattr(dns, "apply", lambda servers: (calls.append(("apply", servers)) or (True, "готово")))
    monkeypatch.setattr(diag, "apply_fix", lambda fix: (calls.append(("fix", fix)) or "ок"))
    monkeypatch.setattr(diag, "dns_poisoned", lambda: [])
    text = dns.repair(lambda m: None)
    assert "готово" in text and "Подмены DNS больше нет" in text
    assert ("fix", "browser_doh") in calls and ("fix", "hosts_update") in calls


def test_repair_without_working_resolver_keeps_dns(monkeypatch):
    from app import diag
    monkeypatch.setattr(dns, "best", lambda log=print: None)
    monkeypatch.setattr(diag, "apply_fix", lambda fix: "ок")
    monkeypatch.setattr(diag, "dns_poisoned", lambda: ["ya.ru"])
    text = dns.repair(lambda m: None)
    assert "системный DNS не меняю" in text and "осталась" in text
