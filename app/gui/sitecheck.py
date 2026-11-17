"""Сайт «не открывается»: проверка без GUI, чтобы ею пользовались и окно, и тесты."""
import socket
import time

from app import diag, lists, tester


def _with_direct_path(fn):
    """Выполнить проверку мимо VPN и вернуть BIND как был: иначе он останется для следующих проб."""
    from app import vpn
    old = tester.BIND
    tester.BIND = vpn.bind_ip() if tester.vpn_active() else None
    try:
        return fn()
    finally:
        tester.BIND = old


def _site_check(dom: str, running: bool) -> str:
    """«Сайт не открывается»: нужен ли обход, мешает ли он или дело не в нём. Без GUI, возвращает текст."""
    if not running:
        return "Сначала включите обход (большая кнопка), потом проверьте сайт."
    try:
        socket.gethostbyname(dom)
    except socket.gaierror:
        try:
            exists = bool(diag._doh(dom))
        except OSError:
            exists = False
        if exists:  # ISPs answer "no such domain" for blocked sites: the bypass can't fix DNS
            return (f"Провайдер не даёт найти {dom}: DNS отвечает, что такого сайта нет, хотя он есть. "
                    "Включите защищённый DNS: «Диагностика» → «Найти проблемы» → «Подмена DNS» → «Исправить».")
        return f"Сайт {dom} не найден, проверьте адрес."
    t = tester.Target("site", f"https://{dom}")
    if tester.probe(t) is not None:
        return f"{dom} уже открывается, обход для него не нужен."
    added = lists.add_user_domain(dom)
    time.sleep(1.0)  # winws re-reads the changed list by itself (on the next packet): no restart
    if any(tester.probe(t) is not None for _ in range(3)):  # first tries through DPI can be reset
        return f"Готово: {dom} добавлен в обход и открывается."
    if added:
        lists.remove_user_domain(dom)
    # the other way round: the bypass itself may break it (Cloudflare/Amazon ranges in the IP list,
    # sign-in pages, launchers), try with the site excluded from every profile
    excluded = lists.add_user_domain(dom, "list-exclude-user.txt")
    time.sleep(1.0)
    if any(tester.probe(t) is not None for _ in range(2)):
        return (f"Готово: {dom} ломался из-за обхода, теперь обход его не трогает, и сайт открывается. "
                "Отменить: «Списки» → «Сайты, которые обход не трогает».")
    if excluded:
        lists.remove_user_domain(dom, "list-exclude-user.txt")
    if tester.probe(tester.Target("ip", f"tcp://{dom}:443")) is None:
        return f"{dom} заблокирован по IP-адресу, zapret тут не поможет, нужен VPN. [ip]"
    return f"Не помогло для {dom}. Попробуйте «Подобрать заново» на главной."
