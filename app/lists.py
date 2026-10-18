"""User list helpers and ipset update."""
import re
import shutil
import urllib.request

import requests

from . import paths

IPSET_URL = ("https://raw.githubusercontent.com/Flowseal/zapret-discord-youtube/refs/heads/main/"
             ".service/ipset-service.txt")
_IP_RE = re.compile(r"^(\d{1,3}(\.\d{1,3}){3}|[0-9a-fA-F:]+:[0-9a-fA-F:]*)(/\d{1,3})?$")


def update_ipset(log=print) -> bool:
    try:
        req = urllib.request.Request(IPSET_URL, headers={"User-Agent": "ZapretAuto"})
        with urllib.request.urlopen(req, timeout=30) as r:
            text = r.read().decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        log(f"ipset: ошибка загрузки: {e}")
        return False
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    if not lines:
        log("ipset: пустой ответ")
        return False
    bad = [l for l in lines if not _IP_RE.match(l)]
    if bad:
        log(f"ipset: некорректные строки ({len(bad)}), например {bad[0][:60]!r}")
        return False
    paths.LISTS.mkdir(parents=True, exist_ok=True)
    dst = paths.LISTS / "ipset-all.txt"
    if dst.exists():
        shutil.copy2(dst, dst.with_name(dst.name + ".bak"))
    dst.write_text("\n".join(lines) + "\n", encoding="utf-8")
    # keep sync_lists from reverting it to the vendor copy
    (paths.LISTS / "ipset-all.txt.custom").write_text("updated from GitHub\n", encoding="utf-8")
    log(f"ipset: обновлено, записей: {len(lines)}")
    return True


def user_list_paths() -> dict:
    return {
        "list-general-user.txt": paths.LISTS / "list-general-user.txt",
        "list-exclude-user.txt": paths.LISTS / "list-exclude-user.txt",
        "ipset-exclude-user.txt": paths.LISTS / "ipset-exclude-user.txt",
        "games_manual": paths.DATA / "games_manual.txt",
    }


TELEGRAM_CIDR_URL = "https://core.telegram.org/resources/cidr.txt"
# fallback: Telegram's published IPv4 ranges (core.telegram.org itself is often unreachable)
TELEGRAM_CIDRS = [
    "91.105.192.0/23", "91.108.4.0/22", "91.108.8.0/22", "91.108.12.0/22", "91.108.16.0/22",
    "91.108.20.0/22", "91.108.56.0/22", "95.161.64.0/20", "149.154.160.0/20", "185.76.151.0/24",
]


def set_telegram(on: bool, log=print) -> None:
    """Fill (or clear) data/lists/ipset-telegram.txt, which every ipset profile also reads."""
    f = paths.LISTS / "ipset-telegram.txt"
    paths.LISTS.mkdir(parents=True, exist_ok=True)
    if not on:
        f.write_text("203.0.113.113/32\n", encoding="utf-8")
        return
    cidrs = TELEGRAM_CIDRS
    try:
        r = requests.get(TELEGRAM_CIDR_URL, timeout=8)
        got = [l.strip() for l in r.text.splitlines() if re.match(r"^\d+\.\d+\.\d+\.\d+/\d+$", l.strip())]
        if r.ok and len(got) >= 5:
            cidrs = got
    except requests.RequestException:
        pass
    f.write_text("\n".join(cidrs) + "\n", encoding="utf-8")
    log(f"Telegram: {len(cidrs)} подсетей добавлено в обход")
