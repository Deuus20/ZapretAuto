"""Целостность файлов обхода: сверка vendor с MANIFEST.sha256.

Файлы обхода (winws.exe, WinDivert) запускаются с правами администратора, и подменить их на диске до
запуска сейчас ничто не мешало. Манифест пишется при сборке и после каждого обновления zapret, поэтому
изменение winws.exe или драйвера видно при старте и в «Диагностике».

Мягкий режим: если манифеста нет (обновление со старой версии), он создаётся по текущему состоянию и
пользователь ничего не видит, иначе после обновления программы все получили бы предупреждение.
Список lists/ намеренно не проверяется: ipset-all.txt обновляется службой и меняется штатно.
"""
from . import paths
from .signing import file_sha256

MANIFEST = "MANIFEST.sha256"
# что проверяем: исполняемые файлы обхода, драйвер, стратегии и версия vendor
PATTERNS = ("bin/*", "general*.bat", "VERSION")


def manifest_path(vendor=None):
    return (vendor or paths.VENDOR) / MANIFEST


def _files(vendor) -> list:
    out = []
    for pattern in PATTERNS:
        out += [p for p in vendor.glob(pattern) if p.is_file()]
    return sorted(out)


def write_manifest(vendor=None) -> int:
    """Записать манифест по текущему состоянию vendor; вернуть число файлов."""
    vendor = vendor or paths.VENDOR
    lines = [f"{file_sha256(p)}  {p.relative_to(vendor).as_posix()}" for p in _files(vendor)]
    manifest_path(vendor).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return len(lines)


def changed(vendor=None) -> list[str] | None:
    """Файлы, которые не сходятся с манифестом (изменены, пропали или лишние). None, манифеста нет."""
    vendor = vendor or paths.VENDOR
    path = manifest_path(vendor)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    want = {}
    for line in text.splitlines():
        sha, _, name = line.partition("  ")
        if sha and name:
            want[name] = sha.strip().lower()
    bad = []
    for name, sha in want.items():
        p = vendor / name
        try:
            if file_sha256(p) != sha:
                bad.append(name)
        except OSError:
            bad.append(name)
    for p in _files(vendor):  # лишний файл в bin/ или новый general*.bat, тоже расхождение
        name = p.relative_to(vendor).as_posix()
        if name not in want:
            bad.append(name)
    return sorted(set(bad))


_SEEN = "vendor_manifest_seen"  # хэши манифестов, которые мы уже видели (data/settings.json)


def _vkey(vendor) -> str:
    return str(vendor).replace("\\", "/").lower()


def _seen_sha(vendor) -> str | None:
    from .settings import load_settings
    return (load_settings().get(_SEEN) or {}).get(_vkey(vendor))


def _remember(vendor, sha: str | None) -> None:
    from .settings import load_settings, save_settings
    seen = dict(load_settings().get(_SEEN) or {})
    key = _vkey(vendor)
    if sha is None:
        seen.pop(key, None)
    elif seen.get(key) == sha:
        return
    else:
        seen[key] = sha
    save_settings(**{_SEEN: seen})


def ensure(vendor=None) -> list[str] | None:
    """Проверить и, если манифеста ещё нет, создать его по текущему состоянию (мягкий режим).

    Мягкий режим нужен после обновления со старой версии. Но если манифест раньше был (мы запомнили его
    хэш), а теперь исчез, это не «первый запуск», а повод показать расхождение: иначе удаление манифеста
    отключало бы проверку файлов обхода."""
    vendor = vendor or paths.VENDOR
    bad = changed(vendor)
    if bad is None:
        if _seen_sha(vendor):
            return [MANIFEST]
        try:
            write_manifest(vendor)
            _remember(vendor, file_sha256(manifest_path(vendor)))
        except OSError:
            return None
        return None
    try:
        _remember(vendor, file_sha256(manifest_path(vendor)))
    except OSError:
        pass  # файл настроек занят: запомнить не вышло, но саму проверку файлов это не ломает
    return bad
