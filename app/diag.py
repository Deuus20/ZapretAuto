"""Environment diagnostics for winws/WinDivert."""
import ctypes
import re
import subprocess
import time

import psutil

from . import paths, vendorcheck

NOWIN = 0x08000000  # CREATE_NO_WINDOW
CONFLICT = ["GoodbyeDPI", "Killer", "SmartByte", "TracSrvWrapper", "discordfix_zapret", "winws1", "winws2"]
SERVICES = ("BFE", "zapret", "WinDivert", *CONFLICT)
# Скрипты PowerShell лежат только здесь, а вызывающий код передаёт имя, а не текст: подставленная снаружи строка
# не может стать кодом (находка 15 отчёта). Состояние всех служб читается одним вызовом, раньше на каждый
# прогон диагностики уходило 10 процессов вместо одного.
_SVC_NAMES = ",".join(f"'{n}'" for n in SERVICES)
SCRIPTS: dict[str, str] = {
    # Get-Service -Name со списком падает на первом же отсутствующем имени (а winws1/winws2 обычно нет):
    # берём все службы и фильтруем сами. Отсутствующие просто не попадут в ответ.
    "services": (f"$want=@({_SVC_NAMES}); Get-Service -ErrorAction SilentlyContinue | "
                 "Where-Object { $want -contains $_.Name } | "
                 "ForEach-Object { $_.Name + '=' + $_.Status }"),
    "timestamps": "(Get-NetTCPSetting -SettingName Internet).Timestamps",
    "doh": (r"$n=0; $p='HKLM:\System\CurrentControlSet\Services\Dnscache\InterfaceSpecificParameters'; "
            r"if(Test-Path $p){ Get-ChildItem $p -Recurse -ErrorAction SilentlyContinue | ForEach-Object { "
            r"$v=(Get-ItemProperty $_.PSPath -ErrorAction SilentlyContinue).DohFlags; if($v -gt 0){$n++} } }; "
            r"$a=(Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Services\Dnscache\Parameters' "
            r"-EA SilentlyContinue).EnableAutoDoh; "
            r"$d=((Get-DnsClientServerAddress -AddressFamily IPv4 -EA SilentlyContinue).ServerAddresses -join ' '); "
            "\"doh=$n;auto=$a;dns=$d\""),
    # адаптер с маршрутом по умолчанию: ему и меняется DNS; имена серверов берём из вывода, а не от пользователя
    "adapter": "(Get-NetIPConfiguration | Where-Object { $_.IPv4DefaultGateway -ne $null } | "
               "Select-Object -First 1).InterfaceAlias",
    "dnsservers": "Get-DnsClientServerAddress -AddressFamily IPv4 -EA SilentlyContinue | "
                  "Where-Object { $_.ServerAddresses } | ForEach-Object { "
                  "$_.InterfaceAlias + '=' + ($_.ServerAddresses -join ',') }",
}
def _decode(b: bytes, oem: int | None = None) -> str:
    """Console programs (sc, net, taskkill, powershell) print in the OEM code page, not UTF-8: decoding their
    Russian messages as UTF-8 left «������» in the diagnostics dialog."""
    cp = oem or ctypes.windll.kernel32.GetOEMCP()
    for enc in ("utf-8", f"cp{cp}", "cp1251"):
        try:
            return b.decode(enc)
        except UnicodeDecodeError:
            continue
    return b.decode("utf-8", "replace")
def _run(args: list[str], timeout=20) -> tuple[int, str]:
    try:
        r = subprocess.run(args, capture_output=True, timeout=timeout, creationflags=NOWIN)
        return r.returncode, _decode(r.stdout) + _decode(r.stderr)
    except Exception as e:  # noqa: BLE001
        return -1, str(e)
def _ps(name: str) -> tuple[int, str]:
    """Запустить один из SCRIPTS по имени. Текст команды берётся только оттуда."""
    return _run(["powershell", "-NoProfile", "-NonInteractive", "-Command", SCRIPTS[name]])
_svc_cache: dict[str, str] | None = None
def _svc_status(name: str) -> str | None:
    """Service status string (Running/Stopped/...) or None if absent. Все службы читаются одним вызовом."""
    if name not in SERVICES:
        raise ValueError(f"служба {name!r} не проверяется")
    global _svc_cache
    if _svc_cache is None:
        rc, out = _ps("services")
        _svc_cache = {}
        if rc == 0:
            for line in out.splitlines():
                k, _, v = line.partition("=")
                if k.strip() and v.strip():
                    _svc_cache[k.strip()] = v.strip()
    return _svc_cache.get(name)
def _item(label, level, detail="", fix=None, fix_label=None):
    r = {"label": label, "level": level, "detail": detail, "fix": fix}
    if fix_label:
        r["fix_label"] = fix_label  # button text when "Исправить" would mislead (e.g. «Убрать»)
    return r
def _procs() -> set[str]:
    return {(p.info["name"] or "").lower() for p in psutil.process_iter(["name"])}
BYPASS_FILES = ("winws.exe", "WinDivert.dll", "WinDivert64.sys", "cygwin1.dll")
def missing_files() -> list[str]:
    """Bypass binaries that are gone (antivirus quarantine is the usual reason)."""
    return [f for f in BYPASS_FILES if not (paths.BIN / f).is_file()]
def first_problem() -> str | None:
    """Short human-readable cause for a bypass that won't start, or None."""
    for r in run_checks():
        if r["level"] == "err" or (r["level"] == "warn" and r["label"] in ("Конфликтующие службы", "Служба zapret",
                                                                            "Остаточный WinDivert")):
            return f"{r['label']}: {r['detail']}"
    return None
ANYCAST_DOMAINS = ("discord.com", "rutracker.org")  # Cloudflare anycast: same IPs from any resolver
STUB_CHECK_DOMAINS = ("www.youtube.com", "youtube.com")
# JSON DoH endpoints, tried in order: ISPs block some of them (8.8.8.8 since July 2026)
DOH_JSON = ("https://1.1.1.1/dns-query", "https://dns.quad9.net:5053/dns-query", "https://dns.google/resolve")
# DoH templates for browsers, same providers and order (fix_browser_doh picks the first reachable one)
DOH_TEMPLATES = ("https://cloudflare-dns.com/dns-query", "https://dns.quad9.net/dns-query",
                 "https://dns.google/dns-query{?dns}")
def _doh(name: str) -> set[str]:
    """A records of name via DNS-over-HTTPS; raises if no endpoint answers."""
    import requests
    err = None
    for url in DOH_JSON:
        try:
            j = requests.get(url, params={"name": name, "type": "A"}, headers={"accept": "application/dns-json"},
                             timeout=6).json()
            return {a["data"] for a in j.get("Answer", []) if a.get("type") == 1}
        except (requests.RequestException, ValueError) as e:
            err = e
    raise OSError(f"no DoH endpoint answered: {err}")
def dns_poisoned() -> list[str] | None:
    """Domains whose system-resolver answers look forged: bogus IPs, "no such domain" for a domain that exists
    (ISPs answer NXDOMAIN for blocked sites since August 2026), or no overlap with DoH for anycast domains.
    None if DoH itself is unreachable and nothing is obviously wrong."""
    import ipaddress
    import socket
    bad, doh_down = [], False
    for d in ANYCAST_DOMAINS + STUB_CHECK_DOMAINS:
        try:
            sys_ips = set(socket.gethostbyname_ex(d)[2])
        except socket.gaierror:  # "no such host" for a domain that exists: forged, if DNS works at all
            try:
                socket.gethostbyname("ya.ru")  # control: offline / no DNS also fails here
                bad.append(d)
            except OSError:
                pass
            continue
        except OSError:
            continue
        if any((a := ipaddress.ip_address(ip)).is_private or a.is_loopback or a.is_unspecified or a.is_link_local
               for ip in sys_ips):
            bad.append(d)
            continue
        if d in ANYCAST_DOMAINS and not doh_down:
            try:
                doh = _doh(d)
            except Exception:  # noqa: BLE001
                doh_down = True
                continue
            if doh and not (sys_ips & doh):
                bad.append(d)
    return bad if bad or not doh_down else None
_CACHE: tuple[float, list[dict]] | None = None
CACHE_TTL = 10.0  # s: «Найти проблемы» и авто-разбор сбоя идут подряд, но после «Исправить» нужен свежий ответ
def forget() -> None:
    """Забыть прошлый прогон: после исправления проверки должны быть заново."""
    global _CACHE, _svc_cache
    _CACHE, _svc_cache = None, None
def run_checks(fresh: bool = False) -> list[dict]:
    global _CACHE, _svc_cache
    if not fresh and _CACHE and time.monotonic() - _CACHE[0] < CACHE_TTL:
        return _CACHE[1]
    _svc_cache = None
    res = []
    procs = _procs()
    st = _svc_status("BFE")
    res.append(_item("Base Filtering Engine", "ok" if st == "Running" else "err",
                     f"служба: {st or 'не найдена'}" + ("" if st == "Running" else ", WinDivert не заработает")))
    rc, out = _ps("timestamps")
    ts = out.strip().lower()
    if rc == 0 and ts in ("enabled", "disabled"):
        res.append(_item("TCP timestamps", "ok" if ts == "enabled" else "warn", ts,
                         None if ts == "enabled" else "tcp_timestamps"))
    else:
        res.append(_item("TCP timestamps", "warn", "не удалось определить", "tcp_timestamps"))
    missing = missing_files()
    res.append(_item("Файлы обхода", "err" if missing else "ok",
                     ("нет: " + ", ".join(missing) + ", скорее всего, их удалил антивирус. Добавьте папку программы "
                      "в исключения и переустановите ZapretAuto") if missing else "winws.exe, WinDivert на месте"))
    try:
        tampered = vendorcheck.ensure()
    except OSError:
        tampered = None
    if tampered:
        res.append(_item("Файлы обхода изменены", "err",
                         "не совпадают с манифестом: " + ", ".join(tampered[:5]) + ", подмену или правку файлов "
                         "вручную видно по этой сверке; «Исправить» скачает zapret заново", "reinstall_vendor"))
    bad = []
    for n in CONFLICT:
        if _svc_status(n) == "Running" or n.lower() + ".exe" in procs:
            bad.append(n)
    res.append(_item("Конфликтующие службы", "warn" if bad else "ok", ", ".join(bad) or "нет"))
    zs = _svc_status("zapret")
    if zs == "Running":
        res.append(_item("Служба zapret", "warn", "запущена (конфликтует с приложением)", "stop_zapret_service"))
    else:
        res.append(_item("Служба zapret", "ok", zs or "нет"))
    ag = "adguardsvc.exe" in procs
    res.append(_item("AdGuard", "warn" if ag else "ok", "AdguardSvc.exe запущен, может мешать" if ag else "нет"))
    wd = _svc_status("WinDivert")
    ours = _own_windivert()  # game flow capture keeps the driver loaded without winws: that is normal
    stale = (wd == "Running" and "winws.exe" not in procs and "winws2.exe" not in procs and not ours)
    if stale:
        detail = "служба WinDivert работает без winws.exe"
    elif wd == "Running" and ours:
        detail = "используется слежением за играми, так и должно быть"
    else:
        detail = wd or "нет"
    res.append(_item("Остаточный WinDivert", "warn" if stale else "ok", detail,
                     "remove_windivert" if stale else None))
    rc, out = _ps("doh")
    fields = dict(part.split("=", 1) for part in out.strip().split(";") if "=" in part)
    try:
        n = int(fields.get("doh", ""))
    except ValueError:
        n = -1
    if n <= 0:  # Windows 10: auto-DoH via registry + DoH-capable DNS server
        auto, dns = (fields.get("auto") or "").strip(), fields.get("dns") or ""
        if auto == "2" and any(d in dns for d in ("1.1.1.1", "1.0.0.1", "8.8.8.8", "8.8.4.4", "9.9.9.9")):
            n = 1
        else:
            n = max(n, 0)
    if n < 0:
        res.append(_item("Secure DNS (DoH)", "warn", "не удалось определить", "dns_repair"))
    elif n == 0:
        res.append(_item("Secure DNS (DoH)", "warn", "не включён, провайдер может подменять DNS. "
                         "«Исправить» включит DoH в браузерах и поставит рабочий DNS", "dns_repair"))
    else:
        res.append(_item("Secure DNS (DoH)", "ok", "включён"))
    bad = dns_poisoned()
    if bad is None:
        res.append(_item("Подмена DNS", "ok", "не удалось проверить (нет доступа к 1.1.1.1)"))
    elif bad:
        doh_on = _browser_doh_on()
        res.append(_item("Подмена DNS", "err", "провайдер подменяет адреса: " + ", ".join(bad) +
                         (", защищённый DNS в браузерах уже включён, но программам вне браузера нужен рабочий "
                          "системный DNS. " if doh_on else ", обход не поможет без защищённого DNS. ") +
                         "«Исправить» подберёт рабочий DNS для этой сети, включит DoH в браузерах и запишет "
                         "адреса в hosts", "dns_repair"))
    else:
        res.append(_item("Подмена DNS", "ok", "ответы DNS совпадают с DNS-over-HTTPS"))
    try:
        from .tester import vpn_active
        v = vpn_active()
    except Exception:  # noqa: BLE001
        v = None
    from . import vpn as vpnmod
    srv = vpnmod.server_ips()
    if v:
        b = vpnmod.bind_ip()
        res.append(_item("VPN", "ok" if b else "warn",
                         f"весь трафик идёт через {v}; " + ("подбор и проверки идут мимо него" if b else
                                                            "обход zapret бесполезен, пока VPN включён")))
    else:
        res.append(_item("VPN", "ok", ("раздельный туннель или VPN выключен" if srv else "нет") +
                         (f"; серверы VPN исключены из обхода: {', '.join(sorted(srv))}" if srv else "")))
    sac = _smart_app_control()
    if sac == 1:  # Windows 11 Smart App Control "on" blocks unsigned programs: winws.exe is unsigned
        res.append(_item("Smart App Control", "err",
                         "включён, Windows блокирует неподписанные программы, в том числе winws.exe, и обход не "
                         "запускается. Отключите: Параметры → Конфиденциальность и защита → Безопасность Windows → "
                         "Управление приложениями и браузером → Интеллектуальное управление приложениями"))
    try:
        entries = flowseal_hosts()
        have, ours = hosts_state(entries)
    except Exception:  # noqa: BLE001 (GitHub unreachable / hosts unreadable: no verdict)
        entries = None
    if entries:
        dns_bad = bool(bad) and any(d == "discord.com" for d in bad)
        if have == len(entries):
            res.append(_item("Адреса Flowseal в hosts", "ok",
                             f"записаны и актуальны ({have})" + ("" if ours else ", добавлены не этой программой"),
                             "hosts_remove" if ours else None, "Убрать"))
        else:
            res.append(_item("Адреса Flowseal в hosts", "warn" if dns_bad else "ok",
                             (f"актуальных записей {have} из {len(entries)}. " if have else "") +
                             ("Провайдер подменяет DNS для Discord, приложение Discord не использует защищённый DNS "
                              "браузера; «Исправить» запишет в hosts правильные адреса Discord, Telegram и GitHub "
                              "(список Flowseal)" if dns_bad else
                              "если Discord, Telegram или GitHub не находятся, «Исправить» запишет в hosts их "
                              "адреса (список Flowseal)"), "hosts_update"))
    caches = _discord_caches()
    if caches:
        res.append(_item("Кэш Discord", "ok", "если Discord висит на загрузке или проверке обновлений, «Исправить» "
                                              "закроет его и очистит кэш", "discord_cache"))
    quic_off = _quic_disabled()
    if quic_off:
        res.append(_item("QUIC в браузерах", "ok", "отключён политикой, браузеры открывают YouTube по TCP"))
    else:
        blocked = _quic_blocked()
        res.append(_item("QUIC в браузерах", "warn" if blocked else "ok",
                         "QUIC до YouTube не проходит (так ТСПУ режет HTTP/3 с 2026 года): браузер сначала ждёт, "
                         "потом переходит на TCP, видео стартует медленно. «Исправить» отключит QUIC в браузерах"
                         if blocked else "включён и проходит; если YouTube в браузере всё же тормозит, «Исправить»",
                         "disable_quic"))
    _CACHE = (time.monotonic(), res)
    return res
def _quic_blocked() -> bool:
    """HTTP/3 to YouTube fails while HTTPS over TCP works (checked through the running bypass)."""
    try:
        from . import tester
        h3 = tester.probe(tester.Target("YouTubeQUIC", "h3://www.youtube.com/"))
        if h3 is not None:
            return False
        return tester.probe(tester.Target("YouTubeWeb", "https://www.youtube.com/")) is not None
    except Exception:  # noqa: BLE001 (no curl_cffi / no network: no verdict)
        return False
QUIC_POLICIES = (r"SOFTWARE\Policies\Google\Chrome", r"SOFTWARE\Policies\Microsoft\Edge",
                 r"SOFTWARE\Policies\YandexBrowser")
def _quic_disabled() -> bool:
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, QUIC_POLICIES[0]) as k:
            return winreg.QueryValueEx(k, "QuicAllowed")[0] == 0
    except OSError:
        return False
def _disable_quic() -> str:
    """Browsers then use TCP for YouTube, which the bypass handles more reliably. Restart the browser."""
    import winreg
    for path in QUIC_POLICIES:
        with winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, path, 0, winreg.KEY_SET_VALUE) as k:
            winreg.SetValueEx(k, "QuicAllowed", 0, winreg.REG_DWORD, 0)
    return "QUIC отключён для Chrome, Edge и Яндекс.Браузера, перезапустите браузер"
def _smart_app_control() -> int | None:
    """Windows 11 Smart App Control: 0 off, 1 on (enforced), 2 evaluation; None where it doesn't exist."""
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\CI\Policy") as k:
            return int(winreg.QueryValueEx(k, "VerifiedAndReputablePolicyState")[0])
    except (OSError, ValueError):
        return None
FLOWSEAL_HOSTS_URL = ("https://raw.githubusercontent.com/Flowseal/zapret-discord-youtube/refs/heads/main/"
                      ".service/hosts")
HOSTS_BEGIN = "# ZapretAuto: адреса от Flowseal (Discord, Telegram, GitHub), начало"
HOSTS_END = "# ZapretAuto: адреса от Flowseal, конец"
_HOST_RE = re.compile(r"^(?=.{1,253}$)([a-z0-9-]{1,63}\.)+[a-z]{2,63}$")
# We write the system hosts file as admin from a remote list: only these sites (and subdomains) may be
# redirected, so a broken or hijacked upstream file can't point a bank or Windows Update elsewhere.
HOSTS_DOMAINS = ("discord.com", "discord.gg", "discordapp.com", "discordapp.net", "discord.media",
                 "github.com", "githubusercontent.com", "githubassets.com",
                 "telegram.org", "telegram.me", "telegram.dog", "telegram.space", "cdn-telegram.org",
                 "t.me", "tg.dev", "telesco.pe")
def _hosts_allowed(host: str) -> bool:
    return any(host == d or host.endswith("." + d) for d in HOSTS_DOMAINS)
def _hosts_file():
    import os
    from pathlib import Path
    return Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "drivers" / "etc" / "hosts"
def flowseal_hosts() -> list[tuple[str, str]]:
    """(ip, host) pairs Flowseal maintains: right addresses for Discord, Telegram and GitHub when the ISP's DNS
    lies. Only valid IPs and host names pass."""
    import ipaddress

    import requests
    text = requests.get(FLOWSEAL_HOSTS_URL, timeout=15).text
    out = []
    for ln in text.splitlines():
        f = ln.split("#", 1)[0].split()
        if len(f) != 2:
            continue
        try:
            ipaddress.ip_address(f[0])
        except ValueError:
            continue
        if _HOST_RE.match(f[1].lower()) and _hosts_allowed(f[1].lower()):
            out.append((f[0], f[1].lower()))
    if len(out) < 10:
        raise ValueError("список Flowseal пустой или повреждён")
    return out
def _read_hosts() -> tuple[list[str], bool]:
    raw = _hosts_file().read_bytes()
    bom = raw.startswith(b"\xef\xbb\xbf")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("cp1251", errors="replace")
    return text.splitlines(), bom
def _write_hosts(lines: list[str], bom: bool) -> None:
    """Атомарно и с резервной копией: hosts читает вся система, а запись поверх файла, прерванная сбоем или
    антивирусом, может оставить его усечённым, тогда перестанет работать разрешение имён во всей Windows."""
    import os
    import shutil
    p = _hosts_file()
    data = ("\ufeff" if bom else "").encode("utf-8") + ("\r\n".join(lines) + "\r\n").encode()
    try:
        shutil.copy2(p, p.with_name("hosts.zapretauto.bak"))
    except OSError:
        pass  # копия, страховка, а не условие записи
    tmp = p.with_name("hosts.zapretauto.tmp")
    tmp.write_bytes(data)
    try:
        os.replace(tmp, p)
    except OSError:
        time.sleep(0.3)  # файл на миг занят (антивирус, другой установщик): одна повторная попытка
        os.replace(tmp, p)
    _run(["ipconfig", "/flushdns"])
def _strip_block(lines: list[str]) -> list[str]:
    if HOSTS_END not in (ln.strip() for ln in lines):  # end marker lost (edited by hand): drop only our markers,
        return [ln for ln in lines if ln.strip() != HOSTS_BEGIN]  # never the user's lines after it
    out, inside = [], False
    for ln in lines:
        if ln.strip() == HOSTS_BEGIN:
            inside = True
        elif ln.strip() == HOSTS_END:
            inside = False
        elif not inside:
            out.append(ln)
    return out
def hosts_state(entries: list[tuple[str, str]]) -> tuple[int, bool]:
    """(how many of Flowseal's pairs are in hosts, whether our block is there)."""
    lines, _ = _read_hosts()
    have = {tuple(ln.split("#", 1)[0].split()[:2]) for ln in lines if len(ln.split("#", 1)[0].split()) >= 2}
    return sum(1 for e in entries if e in have), any(ln.strip() == HOSTS_BEGIN for ln in lines)
def _hosts_update() -> str:
    """Write Flowseal's pairs as our marked block; older lines for the same names (ours or Flowseal's own
    service.bat) are replaced, everything else in hosts stays."""
    entries = flowseal_hosts()
    names = {h for _, h in entries}
    lines, bom = _read_hosts()
    keep = [ln for ln in _strip_block(lines)
            if not (len(ln.split("#", 1)[0].split()) >= 2 and ln.split("#", 1)[0].split()[1].lower() in names)]
    while keep and not keep[-1].strip():
        keep.pop()
    _write_hosts(keep + ["", HOSTS_BEGIN] + [f"{ip} {h}" for ip, h in entries] + [HOSTS_END], bom)
    return (f"в hosts записаны адреса от Flowseal ({len(entries)}): Discord, Telegram и GitHub откроются, даже если "
            "провайдер подменяет DNS. Перезапустите Discord. Убрать, здесь же, «Убрать»")
def _hosts_remove() -> str:
    lines, bom = _read_hosts()
    if not any(ln.strip() == HOSTS_BEGIN for ln in lines):
        return "в hosts нет записей этой программы"  # don't touch the file for nothing
    _write_hosts(_strip_block(lines), bom)
    return "адреса от Flowseal убраны из hosts"
DISCORD_BUILDS = {"discord": "Discord.exe", "discordptb": "DiscordPTB.exe", "discordcanary": "DiscordCanary.exe",
                  "discorddevelopment": "DiscordDevelopment.exe"}
CACHE_DIRS = ("Cache", "Code Cache", "GPUCache")
def _discord_caches() -> list:
    """Cache folders of installed Discord builds (%APPDATA%\\discord*): a stale cache after blocks kept
    Discord stuck at loading/updating; Flowseal's service.bat clears the same folders."""
    import os
    from pathlib import Path
    base = Path(os.environ.get("APPDATA", ""))
    return [base / b / c for b in DISCORD_BUILDS for c in CACHE_DIRS if (base / b / c).is_dir()]
def _clear_discord_cache() -> str:
    import shutil
    for exe in DISCORD_BUILDS.values():
        _run(["taskkill", "/F", "/IM", exe])
    import time
    time.sleep(1)  # let Discord release its files
    left = []
    for d in _discord_caches():
        shutil.rmtree(d, ignore_errors=True)
        if d.exists():
            left.append(d.name)
    return ("кэш Discord очищен, запустите Discord заново" if not left else
            "часть кэша не удалилась (" + ", ".join(left) + "), закройте Discord и нажмите ещё раз")
def _browser_doh_on() -> bool:
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, QUIC_POLICIES[0]) as k:
            return winreg.QueryValueEx(k, "DnsOverHttpsMode")[0] == "secure"
    except OSError:
        return False
def _browser_doh() -> str:
    """Secure DNS in Chromium browsers via policy, with the first DoH provider this network can reach."""
    import winreg

    import requests
    tpl = None
    for json_url, t in zip(DOH_JSON, DOH_TEMPLATES):
        try:
            requests.get(json_url, params={"name": "youtube.com", "type": "A"},
                         headers={"accept": "application/dns-json"}, timeout=6).json()
            tpl = t
            break
        except (requests.RequestException, ValueError):
            continue
    if not tpl:
        return "не удалось: ни один сервер защищённого DNS не отвечает (возможно, они тоже заблокированы)"
    for path in QUIC_POLICIES:
        with winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, path, 0, winreg.KEY_SET_VALUE) as k:
            winreg.SetValueEx(k, "DnsOverHttpsMode", 0, winreg.REG_SZ, "secure")
            winreg.SetValueEx(k, "DnsOverHttpsTemplates", 0, winreg.REG_SZ, tpl)
    host = tpl.split("/")[2]
    return (f"защищённый DNS ({host}) включён в Chrome, Edge и Яндекс.Браузере, перезапустите браузер. "
            "Браузер покажет «Управляется организацией»: это и есть эта настройка")
SC_HINT = {2: "служба не найдена", 5: "нужны права администратора", 1051: "есть зависимые службы",
           1052: "служба не поддерживает остановку", 1060: "служба не установлена",
           1072: "служба помечена на удаление, завершится после перезагрузки", 1075: "служба отключена"}
def _svc_msg(label: str, rc: int, out: str = "") -> str:
    """Human line for a `sc`/`net` result instead of the raw console text (which is also in another code page)."""
    if rc == 0:
        return f"{label}: готово"
    hint = SC_HINT.get(rc)
    if not hint:
        lines = (out or "").strip().splitlines()
        hint = lines[-1][:160] if lines else "не удалось"
    return f"{label}: код {rc}, {hint}"
def _own_windivert() -> bool:
    """Does this very process hold WinDivert.dll (game flow capture or a filter check)?"""
    try:
        return any("windivert" in m.path.lower() for m in psutil.Process().memory_maps())
    except (psutil.Error, OSError):
        return False
def _remove_windivert() -> str:
    """Delete the leftover WinDivert service. Never while this process keeps the driver loaded: the delete then
    fails with 1072, the service stays marked until a reboot and WinDivert cannot start in the meantime."""
    if _own_windivert():
        return ("служба WinDivert используется самой программой (слежение за играми), удалять её не нужно, "
                "она остановится вместе с программой")
    lines = [_svc_msg("Остановка службы WinDivert", *_run(["sc", "stop", "WinDivert"]))]
    for name in ("WinDivert", "WinDivert14"):
        rc, out = _run(["sc", "delete", name])
        if rc == 1060:  # not installed: nothing to delete
            continue
        lines.append(_svc_msg(f"Удаление службы {name}", rc, out))
    if any("1072" in ln for ln in lines):
        lines.append("Нужна перезагрузка: пока служба помечена на удаление, WinDivert не запустится.")
    return "\n".join(lines)
def _reinstall_vendor() -> str:
    """Скачать zapret заново: манифест целостности потом перезапишется сам."""
    from . import updater
    return "zapret скачан заново" if updater.update(force=True) else "обновлять нечего"
def _dns_repair() -> str:
    """Полная починка DNS: лучший резолвер этой сети, DoH в браузерах, адреса в hosts."""
    from . import dns
    return dns.repair()
def apply_fix(fix_id: str) -> str:
    forget()  # состояние изменится: следующий прогон проверок должен быть новым
    funcs = {"disable_quic": _disable_quic, "browser_doh": _browser_doh, "discord_cache": _clear_discord_cache,
             "hosts_update": _hosts_update, "hosts_remove": _hosts_remove, "remove_windivert": _remove_windivert,
             "reinstall_vendor": _reinstall_vendor, "dns_repair": _dns_repair}
    if fix_id in funcs:
        try:
            return funcs[fix_id]()
        except OSError as e:
            return f"не удалось: {e}"
    cmds = {
        "tcp_timestamps": [["netsh", "interface", "tcp", "set", "global", "timestamps=enabled"]],
        "stop_zapret_service": [["sc", "stop", "zapret"]],
    }
    if fix_id not in cmds:
        return f"неизвестное исправление: {fix_id}"
    out = []
    for c in cmds[fix_id]:
        rc, o = _run(c)
        out.append(_svc_msg(" ".join(c), rc, o))
    return "\n".join(out)
if __name__ == "__main__":
    for r in run_checks(fresh=True):
        print(r)
