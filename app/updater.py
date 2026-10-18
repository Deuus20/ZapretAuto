"""Update vendor/zapret from GitHub releases. Never touches paths.DATA."""
import re
import shutil
import tempfile
import threading
import zipfile
from pathlib import Path

import requests

from . import paths

REPO = "Flowseal/zapret-discord-youtube"
APP_REPO = "yuav2/ZapretAuto"  # our releases with ZapretAuto-Setup.exe


def current_version(vendor: Path = paths.VENDOR) -> str:
    try:
        return (vendor / "VERSION").read_text(encoding="utf-8").strip() or "0"
    except OSError:
        return "0"


def latest_release() -> tuple[str, str]:
    r = requests.get(
        f"https://api.github.com/repos/{REPO}/releases/latest",
        headers={"Accept": "application/vnd.github+json"},
        timeout=15,
    )
    r.raise_for_status()
    j = r.json()
    for a in j.get("assets", []):
        if a.get("name", "").lower().endswith(".zip"):
            return j["tag_name"], a["browser_download_url"]
    raise RuntimeError("no .zip asset in latest release")


def _key(v: str):
    v = v.strip().lstrip("vV")
    m = re.match(r"^(\d+(?:\.\d+)*)(.*)$", v)
    if not m:
        return ((0,), "")
    nums = tuple(int(x) for x in m.group(1).split("."))
    while len(nums) > 1 and nums[-1] == 0:
        nums = nums[:-1]
    return (nums, m.group(2).strip().lower())


def is_newer(a: str, b: str) -> bool:
    """True if version a is newer than b."""
    return _key(a) > _key(b)


def _find_root(base: Path) -> Path | None:
    for exe in base.rglob("winws.exe"):
        if exe.parent.name.lower() == "bin":
            return exe.parent.parent
    return None


def update(log=print, before_replace=None, vendor: Path = paths.VENDOR, force=False) -> bool:
    tag, url = latest_release()
    cur = current_version(vendor)
    if not force and not is_newer(tag, cur):
        log(f"Up to date ({cur})")
        return False
    log(f"Updating {cur} -> {tag}")
    vendor = Path(vendor)
    with tempfile.TemporaryDirectory(prefix="zapret_upd_") as td:
        td = Path(td)
        zpath = td / "release.zip"
        log("Downloading...")
        with requests.get(url, stream=True, timeout=30) as r:
            r.raise_for_status()
            with open(zpath, "wb") as f:
                for chunk in r.iter_content(1 << 16):
                    f.write(chunk)
        ex = td / "x"
        with zipfile.ZipFile(zpath) as z:
            z.extractall(ex)
        root = _find_root(ex)
        if root is None:
            raise RuntimeError("bin/winws.exe not found in archive")
        if not list(root.glob("general*.bat")):
            raise RuntimeError("no general*.bat in archive")
        if before_replace:
            before_replace()
        old = vendor.with_name(vendor.name + ".old")
        if old.exists():
            shutil.rmtree(old)
        had_old = vendor.exists()
        if had_old:
            vendor.rename(old)
        try:
            vendor.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(root), str(vendor))
            (vendor / "VERSION").write_text(tag, encoding="utf-8")
        except BaseException:
            if vendor.exists():
                shutil.rmtree(vendor, ignore_errors=True)
            if had_old:
                old.rename(vendor)
            raise
        if had_old:
            shutil.rmtree(old, ignore_errors=True)
    log(f"Updated to {tag}")
    return True


def app_release() -> tuple[str, str]:
    """(tag, Setup.exe url) of the latest ZapretAuto release."""
    r = requests.get(f"https://api.github.com/repos/{APP_REPO}/releases/latest",
                     headers={"Accept": "application/vnd.github+json"}, timeout=15)
    r.raise_for_status()
    j = r.json()
    for a in j.get("assets", []):
        if a.get("name", "").lower().endswith("setup.exe"):
            return j["tag_name"], a["browser_download_url"]
    raise RuntimeError("no Setup.exe asset in latest app release")


def app_update(log=print) -> bool:
    """Download the new installer and run it silently; it restarts the app. Caller must quit right after."""
    import subprocess, tempfile
    from . import __version__
    tag, url = app_release()
    if not is_newer(tag, __version__):
        return False
    exe = Path(tempfile.gettempdir()) / "ZapretAuto-Setup.exe"
    log(f"скачиваю ZapretAuto {tag}")
    with requests.get(url, stream=True, timeout=60) as r:
        r.raise_for_status()
        with open(exe, "wb") as f:
            for chunk in r.iter_content(1 << 16):
                f.write(chunk)
    subprocess.Popen([str(exe), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/UPDATE=1"],
                     creationflags=0x00000008)  # DETACHED_PROCESS
    return True


def app_check_async(callback) -> None:
    """callback(tag|None, newer) for the app itself."""
    def work():
        from . import __version__
        try:
            tag, _ = app_release()
            callback(tag, is_newer(tag, __version__))
        except Exception:
            callback(None, False)

    threading.Thread(target=work, daemon=True).start()


def check_async(callback) -> None:
    def work():
        try:
            tag, _ = latest_release()
            callback(tag, is_newer(tag, current_version()))
        except Exception:
            callback(None, False)

    threading.Thread(target=work, daemon=True).start()
