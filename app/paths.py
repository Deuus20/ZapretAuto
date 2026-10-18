import shutil
import sys
from pathlib import Path

ROOT = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent.parent
VENDOR = ROOT / "vendor" / "zapret"
BIN = VENDOR / "bin"
DATA = ROOT / "data"
LISTS = DATA / "lists"  # runtime lists dir passed to winws (upstream lists + user/game lists)

USER_LISTS = {
    "ipset-exclude-user.txt": "203.0.113.113/32\n",
    "list-general-user.txt": "# Never leave this file empty\ndomain.example.abc\n",
    "list-exclude-user.txt": "domain.example.abc\n",
    "ipset-games.txt": "203.0.113.113/32\n",
}


def sync_lists() -> None:
    """Copy upstream lists into data/lists, keeping user-owned files."""
    LISTS.mkdir(parents=True, exist_ok=True)
    for f in (VENDOR / "lists").iterdir():
        if f.is_file() and f.name not in USER_LISTS:
            shutil.copy2(f, LISTS / f.name)
    for name, default in USER_LISTS.items():
        p = LISTS / name
        if not p.exists():
            p.write_text(default, encoding="utf-8")
