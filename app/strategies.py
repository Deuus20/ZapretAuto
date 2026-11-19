"""Parse upstream general*.bat into strategies made of `--new`-separated profiles."""
import logging
import os
import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from . import paths

GAME_OFF = "12"  # upstream uses a dummy port when the game filter is disabled
GAME_TCP = "1024-1934,1936-65535"  # game filter TCP minus RTMP (1935): desyncing it broke OBS streams (Flowseal 1.10.3)
GAME_UDP = "1024-65535"
PRESET_PREFIX = "свой: "  # a user preset sits in data/presets and is listed next to the upstream strategies
PRESET_MAX_BYTES = 256 * 1024  # a real general*.bat is 5-8 KB; bigger is not a preset
_BAD_NAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_PORTS = re.compile(r"^\d{1,5}(-\d{1,5})?(,\d{1,5}(-\d{1,5})?)*$")
def valid_ports(s: str) -> bool:
    """'1024-1934,1936-65535': ports 1..65535, ranges low-high, comma-separated (WinDivert/winws syntax)."""
    s = (s or "").replace(" ", "")
    if not _PORTS.match(s):
        return False
    for part in s.split(","):
        lo, _, hi = part.partition("-")
        lo, hi = int(lo), int(hi or lo)
        if not (1 <= lo <= hi <= 65535):
            return False
    return True
def game_ports() -> tuple[str, str]:
    """(TCP, UDP) ports of the game filter: the user's choice (settings game_ports_tcp/udp) or the defaults."""
    from .settings import load_settings
    st = load_settings()
    t, u = (st.get("game_ports_tcp") or "").replace(" ", ""), (st.get("game_ports_udp") or "").replace(" ", "")
    return (t if valid_ports(t) else GAME_TCP), (u if valid_ports(u) else GAME_UDP)
@dataclass
class Profile:
    args: list[str]
    def opt(self, name: str) -> list[str]:
        p = f"--{name}="
        return [a[len(p):] for a in self.args if a.startswith(p)]
    @property
    def group(self) -> str:
        """Target group key; profiles with equal keys are interchangeable when mixing."""
        proto = "tcp" if self.opt("filter-tcp") else "udp"
        ports = (self.opt("filter-tcp") or self.opt("filter-udp") or ["*"])[0]
        if "GameFilter" in ports:
            ports = "game"
        lists = sorted(Path(re.sub(r"%\w+%", "", v.strip('"'))).stem for k in ("hostlist", "ipset") for v in self.opt(k))
        lists = [ln for ln in lists if not ln.endswith("-user")]
        extra = self.opt("filter-l7") + self.opt("hostlist-domains")
        return ":".join([proto, ports, "+".join(lists + extra) or "any"])
class StrategyLike(Protocol):
    """Что нужно от стратегии остальным: имя, профили и argv(). Реализуют Strategy и zapret2.Strategy2."""
    name: str
    profiles: list
    def argv(self, game_tcp: bool = False, game_udp: bool = False, auto_hostlist: bool = False) -> list[str]: ...
@dataclass
class Strategy:
    name: str
    wf_tcp: str
    wf_udp: str
    profiles: list[Profile] = field(default_factory=list)
    path: Path | None = None  # файл-источник; у ручного микса его нет
    def argv(self, game_tcp: bool = False, game_udp: bool = False, auto_hostlist: bool = False) -> list[str]:
        gt, gu = game_ports()
        subs = {
            "%BIN%": str(paths.BIN) + "\\",
            "%LISTS%": str(paths.LISTS) + "\\",
            "%GameFilterTCP%": gt if game_tcp else GAME_OFF,
            "%GameFilterUDP%": gu if game_udp else GAME_OFF,
        }
        def sub(s: str) -> str:
            for k, v in subs.items():
                s = s.replace(k, v)
            return s
        out = [str(paths.BIN / "winws.exe"), f"--wf-tcp={sub(self.wf_tcp)}", f"--wf-udp={sub(self.wf_udp)}"]
        auto_src = None
        for i, p in enumerate(self.profiles):
            if i:
                out.append("--new")
            args = [sub(a).replace('"', "") for a in p.args]
            args.append(f"--ipset-exclude={paths.LISTS / 'ipset-exclude-vpn.txt'}")  # never touch VPN tunnels
            if auto_hostlist and p.group == "tcp:80,443:list-general":
                auto_src = list(args)
            if "ipset-all" in p.group:  # route game ipset through every ipset profile (game ports + 80/443)
                args.insert(1, f"--ipset={paths.LISTS / 'ipset-games.txt'}")
                if p.group != "udp:game:ipset-all":  # Telegram calls must not get the game UDP fakes
                    args.insert(1, f"--ipset={paths.LISTS / 'ipset-telegram.txt'}")
                # games' own web/login servers (CloudFront, Cloudflare...) sit inside ipset-all's huge ranges;
                # desyncing them hung Wardogs at "logging in", gamewatch collects them here
                args.append(f"--ipset-exclude={paths.LISTS / 'ipset-exclude-games.txt'}")
                if any(a.startswith("--filter-tcp") for a in args):  # Steam's client link shares SDR games' nets
                    args.append(f"--ipset-exclude={paths.LISTS / 'ipset-exclude-steam.txt'}")
            out += args
        if auto_src:
            # winws adds domains whose connections fail (RST / retransmits) and desyncs them from then on. Its own
            # profile, last: a --hostlist-auto profile takes every 80/443 connection, so inside the general profile
            # it kept Cloudflare/AWS from the ipset-all profile below it, and the «16 KB» whitelist block (no RST,
            # the transfer just stalls) hung Wardogs' Epic login and any Cloudflare site off the lists
            out += ["--new"] + [a for a in auto_src if not a.startswith(("--hostlist=", "--hostlist-domains="))]
            out += [f"--hostlist-auto={paths.LISTS / 'list-auto.txt'}", "--hostlist-auto-fail-threshold=3"]
        left = sorted({v for a in out for v in re.findall(r"%[\w~]+%", a)})
        if left:
            raise ValueError(f"{self.name}: unknown variables {left}")
        return out
def parse_bat(path: Path) -> Strategy:
    text = path.read_text(encoding="utf-8", errors="replace")
    text = re.sub(r"\^\s*\r?\n", " ", text)
    m = re.search(r'winws\.exe"\s+(.*)', text)
    if not m:
        raise ValueError(f"no winws command in {path.name}")
    tokens = shlex.split(m.group(1).replace("^!", "!"), posix=False)
    wf_tcp = wf_udp = GAME_OFF
    profiles, cur = [], []
    for t in tokens:
        if t.startswith("--wf-tcp="):
            wf_tcp = t[9:]
        elif t.startswith("--wf-udp="):
            wf_udp = t[9:]
        elif t == "--new":
            profiles.append(Profile(cur))
            cur = []
        else:
            cur.append(t)
    if cur:
        profiles.append(Profile(cur))
    name = re.sub(r"^general\s*\(?|\)?$", "", path.stem) or "default"
    return Strategy(name, wf_tcp, wf_udp, profiles, path)


def import_preset(src: str | Path) -> str:
    """Скопировать чужой general*.bat в data/presets, проверив его тем же разбором. Файл не выполняется.

    Возвращает имя, под которым пресет появится в списке стратегий («свой: …»)."""
    src = Path(src)
    if src.suffix.lower() != ".bat":
        raise ValueError("нужен файл .bat")
    if not src.is_file():
        raise ValueError("файл не найден")
    if src.stat().st_size > PRESET_MAX_BYTES:
        raise ValueError("файл больше 256 КБ, это не похоже на пресет zapret")
    raw = src.read_bytes()  # читаем один раз: проверяем и копируем ровно те байты, что прочитали
    if len(raw) > PRESET_MAX_BYTES:
        raise ValueError("файл больше 256 КБ, это не похоже на пресет zapret")
    if "winws.exe" not in raw.decode("utf-8", errors="replace"):
        raise ValueError("в файле нет команды winws.exe: это не пресет zapret")
    s = parse_bat(src)
    if not s.profiles or not any(a.startswith("--filter-") for pr in s.profiles for a in pr.args):
        raise ValueError("в пресете нет ни одного профиля с --filter-")
    s.argv()  # проверяет %BIN%/%LISTS%, порты и неизвестные переменные
    name = re.sub(r"^general\s*\(?|\)?$", "", src.stem).strip() or "preset"
    name = _BAD_NAME.sub("_", name)[:80].strip(" .") or "preset"
    paths.PRESETS.mkdir(parents=True, exist_ok=True)
    dst = paths.PRESETS / f"{name}.bat"
    if dst.exists():  # не затирать чужой пресет молча: пусть пользователь переименует файл
        raise ValueError(f"пресет «{name}» уже есть: переименуйте файл и попробуйте снова")
    dst.write_bytes(raw)
    return PRESET_PREFIX + name
_CACHE: tuple[tuple, list[Strategy]] | None = None
def _sources() -> list[tuple[Path, bool]]:
    """(файл, это свой пресет): сначала штатные стратегии, потом пользовательские."""
    vendor = [(p, False) for p in sorted(paths.VENDOR.glob("general*.bat"))]
    presets = [(p, True) for p in sorted(paths.PRESETS.glob("*.bat"))]
    return vendor + presets
def _stamp() -> tuple:
    """Версия содержимого: имена, mtime и размер general*.bat у Flowseal и *.bat в data/presets. Дешевле разбора
    и меняется после обновления zapret или добавления своего пресета.

    Размер нужен потому, что два обновления в одну системную «тик-секунду» могут дать одинаковый mtime.
    stat в try: пресет могли удалить в проводнике между glob и stat, и это не должно ронять окно."""
    files = []
    for p, preset in _sources():
        try:
            st = p.stat()
        except OSError:
            continue
        files.append((str(p), st.st_mtime_ns, st.st_size, preset))
    return (str(paths.VENDOR), str(paths.PRESETS), *files)
def load_all(log=None) -> list[Strategy]:
    """All parseable strategies; a broken .bat (upstream format change or a hand-made preset) is skipped, not fatal.

    Разбор 22 файлов стоит ~37 мс, а вызывается и при старте обхода, и при заполнении списка в окне: держим
    результат до изменения general*.bat или data/presets (после updater.update() mtime меняется и кэш сам устаревает)."""
    global _CACHE
    stamp = _stamp()
    if _CACHE and _CACHE[0] == stamp:
        return list(_CACHE[1])
    out = []
    used: set[str] = set()
    for p, preset in _sources():
        try:
            s = parse_bat(p)
            if preset:
                s.name = PRESET_PREFIX + s.name
            if not s.profiles or not any(a.startswith("--filter-") for pr in s.profiles for a in pr.args):
                raise ValueError("no profiles")
            s.argv()  # validates variable substitution
            if s.name in used:  # MY.bat и general (MY).bat: два файла с одним именем не должны сливаться
                n = 2
                while f"{s.name} ({n})" in used:
                    n += 1
                s.name = f"{s.name} ({n})"
            used.add(s.name)
            out.append(s)
        except Exception as e:  # noqa: BLE001
            msg = f"{'пресет' if preset else 'стратегия'} {p.name} пропущен: {e}"
            if log:
                log(msg)
            elif str(p) not in _skipped:  # callers pass no log: a broken file went unnoticed
                _skipped.add(str(p))
                logging.getLogger("zapretauto").warning(msg)
    if not out and not _skipped_all:
        _skipped_all.append(1)
        logging.getLogger("zapretauto").error("ни одна стратегия zapret не распознана, формат general*.bat изменился?")
    _CACHE = (stamp, out)
    return list(out)
_skipped: set[str] = set()
_skipped_all: list[int] = []
def mix(name: str, picks: dict[str, Strategy], base: Strategy) -> Strategy:
    """Build a strategy from `base`, replacing each group's profile with the one from picks[group]."""
    profiles = []
    for p in base.profiles:
        src = picks.get(p.group)
        alt = next((q for q in src.profiles if q.group == p.group), None) if src else None
        profiles.append(alt or p)
    return Strategy(name, base.wf_tcp, base.wf_udp, profiles)


def to_bat(s: Strategy) -> str:
    """Собрать general*.bat из стратегии: ручной микс сохраняется пресетом, его можно открыть и поправить."""
    parts = [f"--wf-tcp={s.wf_tcp}", f"--wf-udp={s.wf_udp}"]
    for i, p in enumerate(s.profiles):
        if i:
            parts.append("--new")
        parts.extend(p.args)
    return ("@echo off\r\nchcp 65001 > nul\r\n:: собран в ZapretAuto (ручной микс)\r\n"
            f'"%BIN%winws.exe" {" ".join(parts)}\r\n')


def save_preset(name: str, s: Strategy) -> str:
    """Сохранить стратегию в data/presets (ручной микс) и вернуть имя для списка стратегий.

    Пишем во временный файл и подменяем только после успешного разбора: старый пресет с тем же именем
    не должен пропасть, если новый почему-то не читается."""
    clean = _BAD_NAME.sub("_", name)[:80].strip(" .") or "mix"
    paths.PRESETS.mkdir(parents=True, exist_ok=True)
    dst = paths.PRESETS / f"{clean}.bat"
    tmp = dst.with_name(dst.name + ".tmp")
    tmp.write_text(to_bat(s), encoding="utf-8")
    try:
        check = parse_bat(tmp)  # файл должен читаться обратно, иначе winws не запустится
        check.argv()
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    os.replace(tmp, dst)
    return PRESET_PREFIX + clean


def user_presets() -> list[Strategy]:
    """Только свои пресеты: по ним страница «Микс» показывает список с удалением."""
    return [s for s in load_all() if s.path is not None and s.path.parent == paths.PRESETS]


def delete_preset(name: str) -> bool:
    """Удалить свой пресет по имени из списка («свой: …»). Штатные стратегии не трогаются."""
    s = next((x for x in load_all() if x.name == name), None)
    if s is None or s.path is None or s.path.parent != paths.PRESETS:
        return False
    try:
        s.path.unlink()
    except OSError:
        return False
    return True
