import os
import shutil
import sys
from pathlib import Path

ROOT = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent.parent
VENDOR = ROOT / "vendor" / "zapret"
BIN = VENDOR / "bin"
# portable copy (unpacked zip): updates come as a zip into this folder, not via the installer
PORTABLE = getattr(sys, "frozen", False) and (ROOT / "portable.txt").exists()


def _pick_data() -> Path:
    d = ROOT / "data"
    try:
        d.mkdir(parents=True, exist_ok=True)
        t = d / ".w"
        t.write_text("1")
        t.unlink()
        return d
    except OSError:
        d = Path(os.environ.get("PROGRAMDATA") or "C:/ProgramData") / "ZapretAuto" / "data"
        d.mkdir(parents=True, exist_ok=True)
        _lock_down(d.parent)
        return d


def _lock_down(d: Path) -> None:
    """ProgramData is user-writable by default: allow writes only for Administrators and SYSTEM."""
    import subprocess
    try:
        subprocess.run(["icacls", str(d), "/inheritance:r", "/grant:r", "*S-1-5-32-544:(OI)(CI)F",
                        "*S-1-5-18:(OI)(CI)F", "*S-1-5-32-545:(OI)(CI)RX"],
                       capture_output=True, timeout=10, creationflags=0x08000000)
    except (OSError, subprocess.TimeoutExpired):
        pass


DATA = _pick_data()
LISTS = DATA / "lists"  # runtime lists dir passed to winws (upstream lists + user/game lists)

USER_LISTS = {
    "ipset-exclude-user.txt": "203.0.113.113/32\n",
    "list-general-user.txt": "# Never leave this file empty\ndomain.example.abc\n",
    "list-exclude-user.txt": "domain.example.abc\n",
    "ipset-games.txt": "203.0.113.113/32\n",
    "ipset-telegram.txt": "203.0.113.113/32\n",
    "ipset-exclude-vpn.txt": "203.0.113.113/32\n",
    "ipset-exclude-games.txt": "203.0.113.113/32\n",
    "list-auto.txt": "domain.example.abc\n",
}


# Valve's Steam Datagram Relay nets (AS32590): gamewatch adds them to ipset-games for SDR games (UDP). Steam's own
# client link (CM servers, TCP 27017-27050/443) lives in the same nets: excluded from TCP profiles, desync broke it
# (Shift+Tab overlay and friends would not open with the bypass on).
VALVE_NETS = ("103.10.124.0/23", "103.28.54.0/23", "146.66.152.0/21", "155.133.224.0/19", "162.254.192.0/21",
              "185.25.180.0/22", "205.196.6.0/24")


def sync_lists() -> None:
    """Copy upstream lists into data/lists, keeping user-owned files."""
    LISTS.mkdir(parents=True, exist_ok=True)
    for f in (VENDOR / "lists").iterdir():
        if f.is_file() and f.name not in USER_LISTS:
            if f.name == "ipset-all.txt" and (LISTS / "ipset-all.txt.custom").exists():
                continue
            shutil.copy2(f, LISTS / f.name)
    for name, default in USER_LISTS.items():
        p = LISTS / name
        if not p.exists():
            p.write_text(default, encoding="utf-8")
    steam = LISTS / "ipset-exclude-steam.txt"
    text = "\n".join(VALVE_NETS) + "\n"
    if not steam.exists() or steam.read_text(encoding="utf-8") != text:
        steam.write_text(text, encoding="utf-8")


def write_list(p: Path, text: str) -> None:
    """Replace a list atomically: winws re-reads lists live and must never see a half-written file."""
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, p)
