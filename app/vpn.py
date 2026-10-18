"""Coexistence with VPN clients: never desync VPN tunnels; test around a full-tunnel VPN."""
import re
import subprocess

import psutil

from . import paths

CREATE_NO_WINDOW = 0x08000000
VPN_RE = re.compile(r"vpn|wireguard|amnezia|outline|nekoray|hiddify|openvpn|warp|^(wg|tun|tap)\d*$|\b(tun|tap)\b",
                    re.I)
VPN_PROCS = ("openvpn", "warp-svc", "amnezia", "wireguard", "outline", "nekoray", "hiddify", "sing-box",
             "xray", "v2ray", "clash", "mihomo", "happ", "throne")
PLACEHOLDER = "203.0.113.113/32"


def _routes() -> list[tuple[str, str, str, str]]:
    """IPv4 routes from `route print -4`: (dest, mask, gateway, interface ip)."""
    try:
        out = subprocess.run(["route", "print", "-4"], capture_output=True, text=True, timeout=5,
                             creationflags=CREATE_NO_WINDOW).stdout
    except (OSError, subprocess.TimeoutExpired):
        return []
    rows = []
    for ln in out.splitlines():
        f = ln.split()
        if len(f) >= 5 and all(re.match(r"^\d+\.\d+\.\d+\.\d+$", x) for x in (f[0], f[1], f[3])):
            rows.append((f[0], f[1], f[2], f[3]))
    return rows


def _adapter_of(ip: str) -> str | None:
    return next((n for n, addrs in psutil.net_if_addrs().items() if any(a.address == ip for a in addrs)), None)


def server_ips() -> set[str]:
    """Public endpoints of running VPN clients: host routes via a real gateway (WireGuard/OpenVPN add one
    for their server) plus remote addresses of known VPN processes."""
    ips = {d for d, mask, gw, _ in _routes()
           if mask == "255.255.255.255" and re.match(r"^\d", gw) and not d.startswith(("127.", "224.", "255."))}
    try:
        for c in psutil.net_connections("inet"):
            if c.raddr and c.pid:
                try:
                    name = psutil.Process(c.pid).name().lower()
                except psutil.Error:
                    continue
                if any(k in name for k in VPN_PROCS):
                    ips.add(c.raddr.ip)
    except psutil.Error:
        pass
    return {i for i in ips if ":" not in i}


def write_exclude() -> set[str]:
    """data/lists/ipset-exclude-vpn.txt: every profile skips these, so winws never touches VPN tunnels."""
    ips = server_ips()
    paths.LISTS.mkdir(parents=True, exist_ok=True)
    f = paths.LISTS / "ipset-exclude-vpn.txt"
    text = "\n".join(sorted(ips) or [PLACEHOLDER]) + "\n"
    try:
        if f.read_text(encoding="utf-8") == text:
            return ips  # unchanged: don't touch the file (winws reloads lists on modification)
    except OSError:
        pass
    f.write_text(text, encoding="utf-8")
    return ips


def bind_ip() -> str | None:
    """IP of a physical adapter that has its own default route (to test around a full-tunnel VPN)."""
    best = None
    for d, mask, gw, ifip in _routes():
        if d == "0.0.0.0" and mask == "0.0.0.0" and re.match(r"^\d", gw):
            name = _adapter_of(ifip) or ""
            if not VPN_RE.search(name) and "radmin" not in name.lower():
                best = best or ifip
    return best
