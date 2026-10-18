"""Parse upstream general*.bat into strategies made of `--new`-separated profiles."""
import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path

from . import paths

GAME_OFF = "12"  # upstream uses a dummy port when the game filter is disabled


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
        lists = [l for l in lists if not l.endswith("-user")]
        extra = self.opt("filter-l7") + self.opt("hostlist-domains")
        return ":".join([proto, ports, "+".join(lists + extra) or "any"])


@dataclass
class Strategy:
    name: str
    wf_tcp: str
    wf_udp: str
    profiles: list[Profile] = field(default_factory=list)

    def argv(self, game_tcp: bool = False, game_udp: bool = False) -> list[str]:
        subs = {
            "%BIN%": str(paths.BIN) + "\\",
            "%LISTS%": str(paths.LISTS) + "\\",
            "%GameFilterTCP%": "1024-65535" if game_tcp else GAME_OFF,
            "%GameFilterUDP%": "1024-65535" if game_udp else GAME_OFF,
        }

        def sub(s: str) -> str:
            for k, v in subs.items():
                s = s.replace(k, v)
            return s

        out = [str(paths.BIN / "winws.exe"), f"--wf-tcp={sub(self.wf_tcp)}", f"--wf-udp={sub(self.wf_udp)}"]
        for i, p in enumerate(self.profiles):
            if i:
                out.append("--new")
            args = [sub(a).replace('"', "") for a in p.args]
            if p.group.split(":")[1] == "game":  # route game ipset through game profiles too
                args.insert(1, f"--ipset={paths.LISTS / 'ipset-games.txt'}")
            out += args
        return out


def parse_bat(path: Path) -> Strategy:
    text = path.read_text(encoding="utf-8", errors="replace")
    text = re.sub(r"\^\s*\r?\n", " ", text)
    m = re.search(r'winws\.exe"\s+(.*)', text)
    if not m:
        raise ValueError(f"no winws command in {path.name}")
    tokens = shlex.split(m.group(1), posix=False)
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
    return Strategy(name, wf_tcp, wf_udp, profiles)


def load_all() -> list[Strategy]:
    return [parse_bat(p) for p in sorted(paths.VENDOR.glob("general*.bat"))]


def mix(name: str, picks: dict[str, Strategy], base: Strategy) -> Strategy:
    """Build a strategy from `base`, replacing each group's profile with the one from picks[group]."""
    profiles = []
    for p in base.profiles:
        src = picks.get(p.group)
        alt = next((q for q in src.profiles if q.group == p.group), None) if src else None
        profiles.append(alt or p)
    return Strategy(name, base.wf_tcp, base.wf_udp, profiles)
