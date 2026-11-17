"""Update vendor/zapret from GitHub releases. Never touches paths.DATA."""
import os
import re
import shutil
import tempfile
import threading
import zipfile
from pathlib import Path
from typing import NamedTuple
from urllib.parse import urlparse

import requests

from . import paths, signing, vendorcheck
from .netcache import get_json

REPO = "Flowseal/zapret-discord-youtube"
APP_REPO = "Deuus20/ZapretAuto"  # our releases with ZapretAuto-Setup.exe
def app_raw_urls(name: str) -> list[str]:
    """Where a file from our repo's main branch can be read."""
    return [f"https://raw.githubusercontent.com/{APP_REPO}/main/{name}"]
def current_version(vendor: Path = paths.VENDOR) -> str:
    try:
        return (vendor / "VERSION").read_text(encoding="utf-8").strip() or "0"
    except OSError:
        return "0"
def _stage(prefix: str) -> Path:
    """A fresh dir for downloads under data/ (admin-only when installed): %TEMP% is writable by any process of
    this user, which could swap a checked file before the elevated app runs or installs it."""
    if not paths.staging_trusted():  # ProgramData-каталог, который мы не смогли запереть: туда нельзя качать
        raise RuntimeError("каталог данных не защищён от записи другими пользователями — файлы обновления "
                           f"может подменить кто угодно ({paths.DATA}). Запустите программу от администратора "
                           "или удалите этот каталог: при следующем запуске он создастся заново")
    base = paths.DATA / "tmp"
    base.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix=prefix, dir=base))
def _sha256(f: Path) -> str:
    return signing.file_sha256(f)
def latest_release() -> tuple[str, str, str | None]:
    """(tag, zip url, sha256 from GitHub's asset digest or None)."""
    j = get_json(f"https://api.github.com/repos/{REPO}/releases/latest")
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
                 "githubusercontent.com")
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
def update(log=print, before_replace=None, vendor: Path = paths.VENDOR, force=False, allow_unsigned=False) -> bool:
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
        # winws.exe и драйвер запускаются от администратора: непроверяемый архив не ставим.
        # force снимает только проверку версии (переустановка того же выпуска), но не требование хэша.
        if not digest and not allow_unsigned:
            raise RuntimeError("в релизе zapret нет контрольной суммы (GitHub asset digest) — файл не проверить, "
                               "обновление отменено")
        if digest and _sha256(zpath) != digest:
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
        try:
            vendorcheck.write_manifest(vendor)  # новый vendor — новый манифест целостности
        except OSError as e:  # устаревший манифест даст ложное «файлы обхода изменены» — скажем об этом
            log(f"не удалось записать манифест целостности: {e}")
    finally:
        shutil.rmtree(td, ignore_errors=True)
    log(f"zapret обновлён до {tag}")
    return True
class AppRelease(NamedTuple):
    """Что публикует наш релиз: файл для скачивания и подписанный манифест, где записан его хэш. У старых
    релизов (до 2.2) манифеста нет — такой релиз виден в проверке, но обновиться на него нельзя."""
    tag: str
    name: str
    url: str
    manifest: str | None
    signature: str | None
def app_release() -> AppRelease:
    """Last ZapretAuto release: the installer, or the zip for a portable copy (it must not install itself into
    Program Files). Отсутствие манифеста — не ошибка связи: его проверяет app_update перед установкой."""
    j = get_json(f"https://api.github.com/repos/{APP_REPO}/releases/latest")
    assets = {a.get("name", "").lower(): (a.get("name", ""), a.get("browser_download_url", ""))
              for a in j.get("assets", [])}
    kind = "portable.zip" if paths.PORTABLE else "setup.exe"
    name = next((n for n, (_, u) in assets.items() if n.endswith(kind) and u), None)
    if not name:
        raise RuntimeError(f"no {kind} asset in latest app release")
    return AppRelease(j.get("tag_name", ""), *assets[name],
                      assets.get(signing.MANIFEST, ("", None))[1] or None,
                      assets.get(signing.SIGNATURE, ("", None))[1] or None)
def _get_bytes(url: str, limit: int = 1 << 20) -> bytes:
    """Small release file (manifest, signature) into memory, with a size cap."""
    _check_url(url)
    data = bytearray()
    with requests.get(url, timeout=(15, 60), stream=True) as r:
        r.raise_for_status()
        _check_url(r.url)  # after redirects
        for chunk in r.iter_content(1 << 12):
            data += chunk
            if len(data) > limit:
                raise RuntimeError("манифест релиза слишком велик")
    return bytes(data)
def app_update(log=print) -> bool:
    """Download the new version, verify it against the signed manifest and install it; the app restarts. Caller
    quits after."""
    import subprocess

    from . import __version__
    rel = app_release()
    if not TAG_RE.match(rel.tag) or not is_newer(rel.tag, __version__):
        return False
    if not rel.manifest or not rel.signature:
        raise RuntimeError("в релизе нет подписанного манифеста — обновление не проверить")
    shutil.rmtree(paths.DATA / "tmp", ignore_errors=True)  # earlier updates' files
    d = _stage("app_")
    f = d / rel.name
    log(f"скачиваю ZapretAuto {rel.tag}")
    _download(rel.url, f, log)
    log("проверяю подпись релиза…")
    try:
        man = signing.read_manifest(_get_bytes(rel.manifest), _get_bytes(rel.signature).decode("ascii", "replace"))
        signing.check_release(man, rel.tag, APP_REPO, f)
    except signing.ManifestError as e:
        shutil.rmtree(d, ignore_errors=True)
        raise RuntimeError(f"файл обновления не принят: {e}") from None
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
    """Wait until this app has quit, keep the old program files, copy the new ones over the portable folder
    (data/ and vendor/ stay: zapret updates itself), then start it again in the tray.

    Старые файлы сохраняются рядом (папка `<root>.update-old` с меткой `.zapretauto-backup`), а завершённость
    копирования отмечается файлом update.done: если питание пропадёт посреди копирования, при следующем
    запуске recover_portable() вернёт старые файлы. Чужую папку с похожим именем скрипт не трогает, а копию
    проверяет по коду robocopy: откат из неполной копии хуже, чем отсутствие отката."""
    def q(p) -> str:
        return "'" + str(p).replace("'", "''") + "'"
    old = root.with_name(root.name + ".update-old")
    marker = old / ".zapretauto-backup"
    done = root / "update.done"
    err = root / "update.error.txt"
    script = (f"Wait-Process -Id {os.getpid()} -Timeout 60 -ErrorAction SilentlyContinue; "
              f"if (Test-Path {q(old)}) {{ "
              f"if (-not (Test-Path {q(marker)})) {{ Set-Content -Path {q(err)} -Value 'backup folder is not ours'; "
              f"Start-Process -FilePath {q(root / 'ZapretAuto.exe')} -ArgumentList '--tray'; exit 1 }}; "
              f"Remove-Item -Recurse -Force {q(old)} }}; "
              f"New-Item -ItemType Directory -Force {q(old)} | Out-Null; "
              f"robocopy {q(root)} {q(old)} /E /IS /IT /XD data vendor /XF update.done update.error.txt "
              f"/R:3 /W:2 /NFL /NDL /NJH /NJS | Out-Null; "
              f"if ($LASTEXITCODE -ge 8) {{ Remove-Item -Recurse -Force {q(old)} -ErrorAction SilentlyContinue; "
              f"Set-Content -Path {q(err)} -Value 'backup failed'; "
              f"Start-Process -FilePath {q(root / 'ZapretAuto.exe')} -ArgumentList '--tray'; exit 1 }}; "
              f"New-Item -ItemType File -Force {q(marker)} | Out-Null; "
              f"Remove-Item -Force {q(done)} -ErrorAction SilentlyContinue; "
              f"robocopy {q(new)} {q(root)} /E /IS /IT /XD data vendor /R:5 /W:2 /NFL /NDL /NJH /NJS | Out-Null; "
              f"if ($LASTEXITCODE -ge 8) {{ "
              f"robocopy {q(old)} {q(root)} /E /IS /IT /XD data vendor /R:3 /W:2 /NFL /NDL /NJH /NJS | Out-Null; "
              f"Set-Content -Path {q(err)} -Value 'copy failed, rolled back' }} "
              f"else {{ New-Item -ItemType File -Force {q(done)} | Out-Null }}; "
              f"Start-Process -FilePath {q(root / 'ZapretAuto.exe')} -ArgumentList '--tray'; "
              f"Remove-Item -Recurse -Force {q(tmp)} -ErrorAction SilentlyContinue")
    return ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden", "-Command", script]
def recover_portable(root: Path | None = None) -> bool:
    """Незавершённое портативное обновление: вернуть старые файлы из `<root>.update-old`.

    Чужие папки не трогаем: откат делаем только из копии с нашей меткой `.zapretauto-backup`."""
    root = Path(root) if root else Path(paths.ROOT)
    old = root.with_name(root.name + ".update-old")
    if not old.is_dir() or not (old / ".zapretauto-backup").exists():
        return False
    done = root / "update.done"
    if done.exists() and (root / "ZapretAuto.exe").exists():  # обновление доехало: только убрать копию
        done.unlink(missing_ok=True)
        shutil.rmtree(old, ignore_errors=True)
        return False
    for p in old.rglob("*"):  # копирование оборвалось: возвращаем программу как была
        rel = p.relative_to(old)
        if p.is_file() and rel.parts[0] not in ("data", "vendor"):
            dst = root / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, dst)
    done.unlink(missing_ok=True)
    shutil.rmtree(old, ignore_errors=True)
    return True
APP_CHECK_ERROR = ""  # why the last app check failed, in words for the Updates section
def app_check_async(callback) -> None:
    """callback(tag|None, newer) for the app itself; on failure APP_CHECK_ERROR says why."""
    def work():
        global APP_CHECK_ERROR
        from . import __version__
        try:
            rel = app_release()
            APP_CHECK_ERROR = ""
            callback(rel.tag, is_newer(rel.tag, __version__))
        except requests.HTTPError as e:
            # 404 while the internet works: the project page itself is unavailable (hidden or renamed)
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
