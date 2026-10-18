"""User list helpers and ipset update."""
import re
import shutil
import urllib.request

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
