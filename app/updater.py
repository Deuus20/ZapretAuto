"""Update vendor/zapret from GitHub releases. Never touches paths.DATA."""
import os
import re
import shutil
import tempfile
import threading
import zipfile
from pathlib import Path
from urllib.parse import urlparse
import requests
from . import paths
REPO = "Flowseal/zapret-discord-youtube"
APP_REPO = "yuav2/ZapretAuto"  # our releases with ZapretAuto-Setup.exe
# Mirror on Codeberg (Forgejo): used when GitHub fails (GitHub hid yuav2 as spam on 2026-10-20: 404 for all).
MIRROR = "https://codeberg.org"
MIRROR_REPO = "yuav2/ZapretAuto"
def app_raw_urls(name: str) -> list[str]:
    """Where a file from our repo's main branch can be read: GitHub first, then the Codeberg mirror."""
    return [f"https://raw.githubusercontent.com/{APP_REPO}/main/{name}",
            f"{MIRROR}/{MIRROR_REPO}/raw/branch/main/{name}"]
def current_version(vendor: Path = paths.VENDOR) -> str:
    try:
        return (vendor / "VERSION").read_text(encoding="utf-8").strip() or "0"
    except OSError:
        return "0"
def _stage(prefix: str) -> Path:
    """A fresh dir for downloads under data/ (admin-only when installed): %TEMP% is writable by any process of
    this user, which could swap a checked file before the elevated app runs or installs it."""
    base = paths.DATA / "tmp"
    base.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix=prefix, dir=base))
def _sha256(f: Path) -> str:
    import hashlib
    h = hashlib.sha256()
    with open(f, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()
def latest_release() -> tuple[str, str, str | None]:
    """(tag, zip url, sha256 from GitHub's asset digest or None)."""
    r = requests.get(
        f"https://api.github.com/repos/{REPO}/releases/latest",
        headers={"Accept": "application/vnd.github+json"},
        timeout=15,
    )
    r.raise_for_status()
    j = r.json()
    for a in j.get("assets", []):
        if a.get("name", "").lower().endswith(".zip"):
            dig = a.get("digest") or ""
            return j["tag_name"], a["browser_download_url"], dig[7:].lower() if dig.startswith("sha256:") else None
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
MAX_DOWNLOAD = 200 * 1024 * 1024
TAG_RE = re.compile(r"^v?\d+(\.\d+)*[a-z]?$", re.I)
ALLOWED_HOSTS = ("github.com", "objects.githubusercontent.com", "release-assets.githubusercontent.com",
                 "githubusercontent.com", "codeberg.org")
def _check_url(url: str) -> None:
    host = urlparse(url).hostname or ""
    if urlparse(url).scheme != "https" or not any(host == h or host.endswith("." + h) for h in ALLOWED_HOSTS):
        raise RuntimeError(f"unexpected download host: {host}")
def _download(url: str, dest: Path, log=print) -> None:
    """Download to dest.part with size cap and Content-Length check, then rename."""
    _check_url(url)
    part = dest.with_suffix(dest.suffix + ".part")
    with requests.get(url, stream=True, timeout=(15, 60)) as r:
        r.raise_for_status()
        _check_url(r.url)  # after redirects
        expected = int(r.headers.get("Content-Length") or 0)
        got = 0
        with open(part, "wb") as f:
            for chunk in r.iter_content(1 << 16):
                got += len(chunk)
                if got > MAX_DOWNLOAD:
                    raise RuntimeError("download too large")
                f.write(chunk)
    if expected and got != expected:
        part.unlink(missing_ok=True)
        raise RuntimeError(f"incomplete download ({got} of {expected} bytes)")
    os.replace(part, dest)
def _safe_extract(z: zipfile.ZipFile, dest: Path) -> None:
    total = 0
    root = dest.resolve()
    for m in z.infolist():
        total += m.file_size
        if total > MAX_DOWNLOAD * 3:
            raise RuntimeError("archive too large")
        target = (dest / m.filename).resolve()
        if root not in target.parents and target != root:
            raise RuntimeError(f"unsafe path in archive: {m.filename}")
    z.extractall(dest)
def recover_vendor(vendor: Path = paths.VENDOR) -> None:
    """Undo an update interrupted between renaming the old vendor dir and moving the new one in."""
    old = vendor.with_name(vendor.name + ".old")
    if old.exists() and not (vendor / "bin" / "winws.exe").exists():
        if vendor.exists():
            shutil.rmtree(vendor, ignore_errors=True)
        old.rename(vendor)
def update(log=print, before_replace=None, vendor: Path = paths.VENDOR, force=False) -> bool:
    tag, url, digest = latest_release()
    if not TAG_RE.match(tag):
        raise RuntimeError(f"bad release tag: {tag!r}")
    cur = current_version(vendor)
    if not force and not is_newer(tag, cur):
        log(f"zapret актуален ({cur})")
        return False
    log(f"обновляю zapret {cur} → {tag}")
    vendor = Path(vendor)
    td = _stage("zapret_")
    try:
        zpath = td / "release.zip"
        log("скачиваю…")
        _download(url, zpath, log)
        if digest and _sha256(zpath) != digest:  # winws.exe and the driver run as admin
            raise RuntimeError("контрольная сумма архива zapret не совпала")
        ex = td / "x"
        with zipfile.ZipFile(zpath) as z:
            _safe_extract(z, ex)
        root = _find_root(ex)
        if root is None:
            raise RuntimeError("bin/winws.exe not found in archive")
        if not list(root.glob("general*.bat")) or not (root / "lists").is_dir():
            raise RuntimeError("no general*.bat or lists/ in archive")
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
    finally:
        shutil.rmtree(td, ignore_errors=True)
    log(f"zapret обновлён до {tag}")
    return True
def app_release() -> tuple[str, str, str | None]:
    """(tag, download url, .sha256 url or None) of the latest ZapretAuto release: the installer, or the zip for a
    portable copy (it must not install itself into Program Files)."""
    try:
        r = requests.get(f"https://api.github.com/repos/{APP_REPO}/releases/latest",
                         headers={"Accept": "application/vnd.github+json"}, timeout=15)
        r.raise_for_status()
        j = r.json()
    except (requests.RequestException, ValueError):  # Forgejo answers in the same shape
        r = requests.get(f"{MIRROR}/api/v1/repos/{MIRROR_REPO}/releases/latest", timeout=15)
        r.raise_for_status()
        j = r.json()
    assets = {a.get("name", "").lower(): a["browser_download_url"] for a in j.get("assets", [])}
    kind = "portable.zip" if paths.PORTABLE else "setup.exe"
    url = next((u for n, u in assets.items() if n.endswith(kind)), None)
    if not url:
        raise RuntimeError(f"no {kind} asset in latest app release")
    sha = next((u for n, u in assets.items() if n.endswith(kind + ".sha256")), None)
    return j["tag_name"], url, sha
def app_update(log=print) -> bool:
    """Download the new version, verify it and install it; the app restarts. Caller quits after."""
    import subprocess
    from . import __version__
    tag, url, sha_url = app_release()
    if not TAG_RE.match(tag) or not is_newer(tag, __version__):
        return False
    if not sha_url:
        raise RuntimeError("в релизе нет файла .sha256 — обновление не проверить")
    shutil.rmtree(paths.DATA / "tmp", ignore_errors=True)  # earlier updates' files
    d = _stage("app_")
    f = d / url.rsplit("/", 1)[-1]
    log(f"скачиваю ZapretAuto {tag}")
    _download(url, f, log)
    _check_url(sha_url)
    r = requests.get(sha_url, timeout=15)
    r.raise_for_status()
    _check_url(r.url)  # after redirects, like _download
    want = (r.text.split() or [""])[0].strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", want):
        shutil.rmtree(d, ignore_errors=True)
        raise RuntimeError("файл .sha256 в релизе повреждён — обновление не проверить")
    have = _sha256(f)
    if have != want:
        shutil.rmtree(d, ignore_errors=True)
        raise RuntimeError("контрольная сумма не совпала")
    if paths.PORTABLE:
        new = d / "new"
        with zipfile.ZipFile(f) as z:
            _safe_extract(z, new)
        root = next((p.parent for p in new.rglob("ZapretAuto.exe")), None)
        if root is None:
            raise RuntimeError("в архиве нет ZapretAuto.exe")
        log("архив проверен, обновляю портативную копию…")
        subprocess.Popen(portable_swap_command(root, paths.ROOT, d), creationflags=0x08000000 | 0x00000008)
        return True
    log("установщик проверен, запускаю обновление…")
    subprocess.Popen([str(f), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/UPDATE=1"],
                     creationflags=0x00000008)  # DETACHED_PROCESS
    return True
def portable_swap_command(new: Path, root: Path, tmp: Path) -> list[str]:
    """Wait until this app has quit, copy the new files over the portable folder (data/ and vendor/ stay: zapret
    updates itself), then start it again in the tray."""
    def q(p) -> str:
        return "'" + str(p).replace("'", "''") + "'"
    script = (f"Wait-Process -Id {os.getpid()} -Timeout 60 -ErrorAction SilentlyContinue; "
              f"robocopy {q(new)} {q(root)} /E /IS /IT /XD data vendor /R:5 /W:2 /NFL /NDL /NJH /NJS | Out-Null; "
              f"Start-Process -FilePath {q(root / 'ZapretAuto.exe')} -ArgumentList '--tray'; "
              f"Remove-Item -Recurse -Force {q(tmp)} -ErrorAction SilentlyContinue")
    return ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden", "-Command", script]
APP_CHECK_ERROR = ""  # why the last app check failed, in words for the Updates section
def app_check_async(callback) -> None:
    """callback(tag|None, newer) for the app itself; on failure APP_CHECK_ERROR says why."""
    def work():
        global APP_CHECK_ERROR
        from . import __version__
        try:
            tag, _, _ = app_release()
            APP_CHECK_ERROR = ""
            callback(tag, is_newer(tag, __version__))
        except requests.HTTPError as e:
            # 404 while the internet works: the project page itself is unavailable (it was hidden once)
            APP_CHECK_ERROR = ("страница проекта на GitHub сейчас недоступна" if e.response is not None
                               and e.response.status_code == 404 else "GitHub ответил ошибкой")
            callback(None, False)
        except Exception:
            APP_CHECK_ERROR = "нет связи с GitHub"
            callback(None, False)
    threading.Thread(target=work, daemon=True).start()
def check_async(callback) -> None:
    def work():
        try:
            tag, *_ = latest_release()
            callback(tag, is_newer(tag, current_version()))
        except Exception:
            callback(None, False)
    threading.Thread(target=work, daemon=True).start()
