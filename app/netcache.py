"""Условные запросы к GitHub.

Без токена GitHub даёт 60 запросов в час на IP, и лимит общий на всё, что стоит за этим IP. Ответ 304
(«не изменилось») лимит не тратит, поэтому один и тот же адрес мы спрашиваем с If-None-Match, а свежий
ответ держим в памяти: проверка обновлений раз в 6 часов и нажатия «Проверить» не тратят лимит зря.
Кэш живёт в памяти процесса: после перезапуска будет один обычный запрос, зато на диске нет файла,
который мог бы устареть или быть подменён.
"""
import time

import requests

TTL = 600  # s: чаще одного адреса не спрашиваем, даже если сервер готов ответить 304
_CACHE: dict[str, dict] = {}  # url -> {"etag": str, "time": float, "json": object}


def reset() -> None:
    """Забыть кэш (нужно тестам и когда пользователь просит проверить заново)."""
    _CACHE.clear()


def get_retry(url: str, tries: int = 3, **kw):
    """GET с повторами на таймаутах и 5xx: у GitHub бывают короткие сбои, и одна неудачная попытка не должна
    выглядеть для пользователя как «нет связи». 4xx не повторяем: ответ уже окончательный."""
    last: Exception | None = None
    for i in range(tries):
        try:
            r = requests.get(url, **kw)
            if r.status_code < 500:
                return r
            last = requests.HTTPError(f"GitHub ответил {r.status_code}", response=r)
        except requests.RequestException as e:
            last = e
        if i + 1 < tries:
            time.sleep(0.5 * 2 ** i)
    raise last if last else RuntimeError("запрос не выполнен")


def get_json(url: str, timeout: int = 15, ttl: float = TTL):
    """JSON по адресу: свежий кэш не трогает сеть, устаревший проверяется условным запросом."""
    hit = _CACHE.get(url)
    if hit and time.monotonic() - hit["time"] < ttl:
        return hit["json"]
    headers = {"Accept": "application/vnd.github+json"}
    if hit and hit.get("etag"):
        headers["If-None-Match"] = hit["etag"]
    r = get_retry(url, headers=headers, timeout=timeout)
    if r.status_code == 304 and hit:  # не изменилось: ответ из кэша, лимит не потрачен
        hit["time"] = time.monotonic()
        return hit["json"]
    r.raise_for_status()
    data = r.json()
    etag = (getattr(r, "headers", None) or {}).get("ETag", "")
    _CACHE[url] = {"etag": etag, "time": time.monotonic(), "json": data}
    return data
