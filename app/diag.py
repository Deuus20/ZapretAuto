"""Environment diagnostics for winws/WinDivert."""
import subprocess

import psutil

from . import paths

NOWIN = 0x08000000  # CREATE_NO_WINDOW
CONFLICT = ["GoodbyeDPI", "Killer", "SmartByte", "TracSrvWrapper", "discordfix_zapret", "winws1", "winws2"]


def _run(args: list[str], timeout=20) -> tuple[int, str]:
    try:
        r = subprocess.run(args, capture_output=True, timeout=timeout, creationflags=NOWIN)
        return r.returncode, r.stdout.decode("utf-8", "replace") + r.stderr.decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        return -1, str(e)


def _ps(cmd: str) -> tuple[int, str]:
    return _run(["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd])


def _svc_status(name: str) -> str | None:
    """Service status string (Running/Stopped/...) or None if absent."""
    rc, out = _ps(f"$s=Get-Service -Name '{name}' -ErrorAction SilentlyContinue; if($s){{$s.Status}}")
    return out.strip() or None


def _item(label, level, detail="", fix=None):
    return {"label": label, "level": level, "detail": detail, "fix": fix}


def _procs() -> set[str]:
    return {(p.info["name"] or "").lower() for p in psutil.process_iter(["name"])}


def run_checks() -> list[dict]:
    res = []
    procs = _procs()

    st = _svc_status("BFE")
    res.append(_item("Base Filtering Engine", "ok" if st == "Running" else "err",
                     f"служба: {st or 'не найдена'}" + ("" if st == "Running" else " — WinDivert не заработает")))

    rc, out = _ps("(Get-NetTCPSetting -SettingName Internet).Timestamps")
    ts = out.strip().lower()
    if rc == 0 and ts in ("enabled", "disabled"):
        res.append(_item("TCP timestamps", "ok" if ts == "enabled" else "warn", ts,
                         None if ts == "enabled" else "tcp_timestamps"))
    else:
        res.append(_item("TCP timestamps", "warn", "не удалось определить", "tcp_timestamps"))

    sys_ok = (paths.BIN / "WinDivert64.sys").is_file()
    res.append(_item("WinDivert64.sys", "ok" if sys_ok else "err", str(paths.BIN) if sys_ok else "файл не найден в bin/"))

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
    res.append(_item("AdGuard", "warn" if ag else "ok", "AdguardSvc.exe запущен — может мешать" if ag else "нет"))

    wd = _svc_status("WinDivert")
    stale = wd == "Running" and "winws.exe" not in procs
    res.append(_item("Остаточный WinDivert", "warn" if stale else "ok",
                     "служба WinDivert работает без winws.exe" if stale else (wd or "нет"),
                     "remove_windivert" if stale else None))

    rc, out = _ps(
        r"$n=0; $p='HKLM:\System\CurrentControlSet\Services\Dnscache\InterfaceSpecificParameters'; "
        r"if(Test-Path $p){ Get-ChildItem $p -Recurse -ErrorAction SilentlyContinue | ForEach-Object { "
        r"$v=(Get-ItemProperty $_.PSPath -ErrorAction SilentlyContinue).DohFlags; if($v -gt 0){$n++} } }; $n")
    try:
        n = int(out.strip().splitlines()[-1])
    except Exception:  # noqa: BLE001
        n = -1
    if n <= 0:  # Windows 10: auto-DoH via registry + DoH-capable DNS server
        _, auto = _ps(r"(Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Services\Dnscache\Parameters' -EA SilentlyContinue).EnableAutoDoh")
        _, dns = _ps("(Get-DnsClientServerAddress -AddressFamily IPv4).ServerAddresses -join ' '")
        if auto.strip() == "2" and any(d in dns for d in ("1.1.1.1", "1.0.0.1", "8.8.8.8", "8.8.4.4", "9.9.9.9")):
            n = 1
        else:
            n = max(n, 0)
    if n < 0:
        res.append(_item("Secure DNS (DoH)", "warn", "не удалось определить"))
    elif n == 0:
        res.append(_item("Secure DNS (DoH)", "warn", "не включён — провайдер может подменять DNS"))
    else:
        res.append(_item("Secure DNS (DoH)", "ok", "включён"))

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
    quic_off = _quic_disabled()
    res.append(_item("QUIC в браузерах", "ok",
                     "отключён политикой" if quic_off else
                     "включён — если YouTube в браузере тормозит, а «Проверить доступ» показывает ✓, нажмите «Исправить»",
                     None if quic_off else "disable_quic"))
    return res


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
    return "QUIC отключён для Chrome, Edge и Яндекс.Браузера — перезапустите браузер"


def apply_fix(fix_id: str) -> str:
    if fix_id == "disable_quic":
        try:
            return _disable_quic()
        except OSError as e:
            return f"не удалось: {e}"
    cmds = {
        "tcp_timestamps": [["netsh", "interface", "tcp", "set", "global", "timestamps=enabled"]],
        "stop_zapret_service": [["sc", "stop", "zapret"]],
        "remove_windivert": [["net", "stop", "WinDivert"], ["sc", "delete", "WinDivert"], ["sc", "delete", "WinDivert14"]],
    }
    if fix_id not in cmds:
        return f"неизвестное исправление: {fix_id}"
    out = []
    for c in cmds[fix_id]:
        rc, o = _run(c)
        out.append(f"{' '.join(c)} -> {rc}: {o.strip()[:200]}")
    return "\n".join(out)


if __name__ == "__main__":
    for r in run_checks():
        print(r)
