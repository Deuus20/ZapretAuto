"""Whitelist networks: за провайдером остаются только разрешённые сайты, остальное не открывается никак.

Проверка идёт без обхода: если разрешённые сайты открываются, а заблокированные нет, дальнейший подбор
стратегий бесполезен, нужен VPN или другая сеть. Вердикт осторожный: ошибка «белые списки» увела бы человека
от починки, поэтому при любом сомнении отвечаем «неизвестно».
"""
from . import tester

# сайты, которые провайдер обязан оставлять рабочими: их не блокирует государственный фильтр
ALLOWED = {"Госуслуги": "https://www.gosuslugi.ru", "Налоги": "https://nalog.gov.ru", "Яндекс": "https://ya.ru",
           "VK": "https://vk.com", "Mail.ru": "https://mail.ru"}
# сайты, для которых нужен обход: если хоть один открывается напрямую, фильтра по белому списку нет
BLOCKED = {"Discord": "https://discord.com", "YouTube": "https://www.youtube.com",
           "Telegram": "https://telegram.org", "GitHub": "https://github.com"}
TEXT = ("Похоже, у провайдера включены белые списки: разрешённые сайты открываются, а остальные не открываются "
        "даже без обхода. Подбор стратегии тут не поможет: нужен VPN или другая сеть, например мобильный интернет.")


def verdict(allowed_ok: int, allowed_total: int, blocked_ok: int) -> str:
    """'offline' (ничего не открывается: кабель или VPN), 'ok' (фильтра по списку нет), 'whitelist', 'unknown'."""
    if allowed_ok == 0:
        return "offline"
    if blocked_ok:
        return "ok"
    return "whitelist" if allowed_ok * 2 >= allowed_total else "unknown"


def diag_item(rep: dict) -> dict:
    """Строка для «Диагностики»: белые списки находятся сами, без отдельной кнопки."""
    v = (rep or {}).get("verdict")
    if v == "whitelist":
        return {"label": "Белые списки", "level": "err", "detail": TEXT, "fix": None}
    if v == "ok":
        return {"label": "Белые списки", "level": "ok", "fix": None,
                "detail": "фильтра по списку нет: заблокированные сайты открываются и без обхода"}
    if v == "offline":
        return {"label": "Белые списки", "level": "warn", "fix": None,
                "detail": "проверить нельзя: без обхода не открывается ничего (кабель выключен или VPN)"}
    return {"label": "Белые списки", "level": "warn", "fix": None,
            "detail": "не удалось понять: часть сайтов не ответила, проверьте ещё раз"}


def check() -> dict:
    """Опросить обе группы как есть: вызывать при остановленном обходе и без VPN."""
    tester.BIND = None  # прямой путь: с адресом VPN проверка показала бы его, а не провайдера
    names = list(ALLOWED) + list(BLOCKED)
    urls = list(ALLOWED.values()) + list(BLOCKED.values())
    targets = [tester.Target(f"wl{i}", url) for i, url in enumerate(urls)]
    res = tester.probe_all(targets)
    ok = [res[t.key] is not None for t in targets]
    n = len(ALLOWED)
    return {"verdict": verdict(sum(ok[:n]), n, sum(ok[n:])),
            "allowed": dict(zip(names[:n], ok[:n], strict=True)),
            "blocked": dict(zip(names[n:], ok[n:], strict=True))}
