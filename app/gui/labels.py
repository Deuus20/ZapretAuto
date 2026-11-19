"""Подписи групп целей и короткие имена стратегий: нужны и главному окну, и страницам микса/проверок."""
import re

GROUP_NAMES = {
    "udp:443:list-general": "QUIC",
    "tcp:443:list-google": "Google",
    "tcp:80,443:list-general": "сайты",
    "tcp:80,443,8443:ipset-all": "IP-список",
    "udp:443:ipset-all": "QUIC IP-список",
    "udp:19294-19344,50000-50100:discord,stun": "голос Discord",
    "udp:other": "STUN",
    # варианты из EXP и пользовательских пресетов
    "udp:*:list-general+quic": "QUIC",
    "udp:19294-19344,50000-50100:discord,stun,unknown": "голос Discord",
    "tcp:2053,2083,2087,2096,8443:discord.media": "Discord медиа",
}


def group_name(key: str) -> str:
    """Подпись группы для человека; незнакомую показываем коротко, а не теряем."""
    if key in GROUP_NAMES:
        return GROUP_NAMES[key]
    proto, _, rest = key.partition(":")
    ports, _, extra = rest.partition(":")
    if ports == "game":
        name = "игры " + proto.upper()
    elif len(ports) > 20:  # длинный список портов называть нечем, он ничего не скажет
        name = proto.upper() + " порты"
    else:
        name = f"{proto.upper()} {ports}"
    if extra:
        shown = extra.replace("+", ", ")
        name += " · " + (shown[:27] + "…" if len(shown) > 28 else shown)
    return name


def short_name(name: str) -> str:
    """«FAKE TLS AUTO ALT» → «FTAA»: в строке статуса важна компактность, полное имя - в подсказке.
    Имена без пробелов (ALT8, EXP) остаются как есть, цифры в конце сохраняются (ALT 2 → ALT2)."""
    words = re.findall(r"[A-Za-zА-Яа-я0-9]+", name)
    if len(words) <= 1:
        return name
    alpha = [w for w in words if not w.isdigit()]
    digits = "".join(w for w in words if w.isdigit())
    tail = re.search(r"(\d+)$", words[-1])
    if tail and not words[-1].isdigit():  # «FAKE TLS AUTO ALT2» → FTAA2: варианты не должны сливаться
        digits += tail.group(1)
    if len(alpha) == 1:  # «ALT 2» → «ALT2», а не «A2»
        return (alpha[0] + digits).upper()
    return ("".join(w[0] for w in alpha) + digits).upper()
