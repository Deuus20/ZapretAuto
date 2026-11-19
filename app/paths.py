import os
import shutil
import sys
from pathlib import Path

ROOT = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent.parent
VENDOR = ROOT / "vendor" / "zapret"
BIN = VENDOR / "bin"
# portable copy (unpacked zip): updates come as a zip into this folder, not via the installer
PORTABLE = getattr(sys, "frozen", False) and (ROOT / "portable.txt").exists()
DATA_LOCKED = False  # ProgramData-копия: удалось ли оставить запись только администраторам (см. _lock_down)
def _suspicious(d: Path) -> str | None:
    """Почему каталогу нельзя доверять файлы обновления, или None.

    Каталог данных в ProgramData по умолчанию может создать любой пользователь, в том числе заранее, и
    подменить его junction'ом на свою папку. Повышенный процесс пишет туда список загрузки и запускает из
    него установщик, поэтому путь, который ведёт не туда, чем кажется, это чужой код с правами админа."""
    try:
        if d.is_symlink():
            return "это символическая ссылка"
        if d.is_junction():
            return "это junction"
    except OSError:
        return "путь не проверяется"
    return None
def _lock_down(d: Path) -> bool:
    """ProgramData is user-writable by default: allow writes only for Administrators and SYSTEM.

    icacls /grant:r не годится: он не убирает явные ACE, которые кто-то успел поставить заранее, а
    /inheritance:r снимает только унаследованные. Поэтому DACL заменяется целиком, а потом читается обратно:
    True, только если запись осталась лишь у администраторов и владелец каталога не чужой пользователь."""
    if _suspicious(d) or not _is_admin():
        return False
    try:
        _set_dacl(d)
    except OSError:
        return False
    return _data_dir_safe(d)
_OWNERS = ("S-1-5-32-544", "S-1-5-18")  # BUILTIN\Administrators, NT AUTHORITY\SYSTEM
_USERS_SID = "S-1-5-32-545"              # BUILTIN\Users: чтение и запуск, без записи
_READ_MASK = 0x001200A9                  # FILE_GENERIC_READ|FILE_GENERIC_EXECUTE
_WRITE_MASK = 0x00000002 | 0x00000004 | 0x00000010 | 0x00000040 | 0x00000100 | 0x10000000 | 0x40000000 | 0x80000000
_SDDL = "D:P(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)(A;OICI;GR;;;BU)"
_SE_FILE_OBJECT = 1
_OWNER_INFO = 0x00000001
_DACL_INFO = 0x00000004
def _advapi():
    import ctypes
    return ctypes.WinDLL("advapi32", use_last_error=True)
def _free(ptr) -> None:
    import ctypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.LocalFree.argtypes = [ctypes.c_void_p]
    k32.LocalFree(ptr)
def _sid_str(sid) -> str:
    """SID -> «S-1-5-32-544»: имена групп локализованы, сравнивать их нельзя."""
    import ctypes
    from ctypes import wintypes
    adv = _advapi()
    adv.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
    adv.ConvertSidToStringSidW.restype = wintypes.BOOL
    out = wintypes.LPWSTR()
    if not adv.ConvertSidToStringSidW(sid, ctypes.byref(out)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return out.value or ""
    finally:
        _free(out)
def _security(path: Path, info: int):
    """(owner, dacl, sd) каталога; sd освобождает вызывающий."""
    import ctypes
    from ctypes import wintypes
    adv = _advapi()
    adv.GetNamedSecurityInfoW.argtypes = [wintypes.LPWSTR, ctypes.c_int, wintypes.DWORD,
                                         ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p),
                                         ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p),
                                         ctypes.POINTER(ctypes.c_void_p)]
    adv.GetNamedSecurityInfoW.restype = wintypes.DWORD
    owner, dacl, sd = ctypes.c_void_p(), ctypes.c_void_p(), ctypes.c_void_p()
    rc = adv.GetNamedSecurityInfoW(str(path), _SE_FILE_OBJECT, info,
                                   ctypes.byref(owner) if info & _OWNER_INFO else None, None,
                                   ctypes.byref(dacl) if info & _DACL_INFO else None, None, ctypes.byref(sd))
    if rc:
        raise ctypes.WinError(rc)
    return owner, dacl, sd
def _aces(dacl) -> list[tuple[str, int]]:
    """Разрешённые ACE: (SID, маска прав)."""
    import ctypes
    from ctypes import wintypes
    class _Size(ctypes.Structure):
        _fields_ = [("AceCount", wintypes.DWORD), ("AclBytesInUse", wintypes.DWORD),
                    ("AclBytesFree", wintypes.DWORD)]
    class _Header(ctypes.Structure):
        _fields_ = [("AceType", ctypes.c_ubyte), ("AceFlags", ctypes.c_ubyte), ("AceSize", ctypes.c_ushort)]
    class _Allow(ctypes.Structure):
        _fields_ = [("Header", _Header), ("Mask", wintypes.DWORD), ("SidStart", wintypes.DWORD)]
    adv = _advapi()
    adv.GetAclInformation.argtypes = [ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.c_int]
    adv.GetAclInformation.restype = wintypes.BOOL
    size = _Size()
    if not adv.GetAclInformation(dacl, ctypes.byref(size), ctypes.sizeof(size), 2):
        raise ctypes.WinError(ctypes.get_last_error())
    adv.GetAce.argtypes = [ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p)]
    adv.GetAce.restype = wintypes.BOOL
    out = []
    for i in range(size.AceCount):
        ace = ctypes.c_void_p()
        if not adv.GetAce(dacl, i, ctypes.byref(ace)) or not ace.value:
            continue
        allow = ctypes.cast(ace, ctypes.POINTER(_Allow)).contents
        if allow.Header.AceType != 0:  # 0, ACCESS_ALLOWED: остальные (deny, audit) запись не дают
            continue
        out.append((_sid_str(ctypes.c_void_p(ace.value + 8)), int(allow.Mask)))
    return out
def _writable_by_others(d: Path) -> bool:
    """Есть ли в DACL разрешение на запись кому-то, кроме администраторов и SYSTEM (fail-closed при ошибке)."""
    try:
        _, dacl, sd = _security(d, _DACL_INFO)
    except OSError:
        return True
    try:
        if not dacl:
            return True  # DACL нет, писать может любой
        for sid, mask in _aces(dacl):
            if sid in _OWNERS:
                continue
            if sid == _USERS_SID and not mask & ~_READ_MASK:
                continue
            if mask & _WRITE_MASK:
                return True
        return False
    finally:
        _free(sd)
def _current_sid() -> str:
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    adv = _advapi()
    k32.GetCurrentProcess.restype = wintypes.HANDLE
    adv.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    adv.OpenProcessToken.restype = wintypes.BOOL
    adv.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
                                        ctypes.POINTER(wintypes.DWORD)]
    adv.GetTokenInformation.restype = wintypes.BOOL
    tok = wintypes.HANDLE()
    if not adv.OpenProcessToken(k32.GetCurrentProcess(), 0x0008, ctypes.byref(tok)):
        return ""
    try:
        need = wintypes.DWORD()
        adv.GetTokenInformation(tok, 1, None, 0, ctypes.byref(need))  # TokenUser
        buf = ctypes.create_string_buffer(max(need.value, 16))
        if not adv.GetTokenInformation(tok, 1, buf, need, ctypes.byref(need)):
            return ""
        sid = ctypes.cast(buf, ctypes.POINTER(ctypes.c_void_p))[0]
        return _sid_str(sid)
    except OSError:
        return ""
    finally:
        k32.CloseHandle(tok)
def _is_admin() -> bool:
    """Только администратор может выставить DACL и продолжить писать в каталог, который сам и запер."""
    import ctypes
    try:
        return bool(ctypes.WinDLL("shell32").IsUserAnAdmin())
    except OSError:
        return False
def _set_dacl(d: Path) -> None:
    import ctypes
    from ctypes import wintypes
    adv = _advapi()
    adv.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p]
    adv.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = wintypes.BOOL
    sd = ctypes.c_void_p()
    if not adv.ConvertStringSecurityDescriptorToSecurityDescriptorW(_SDDL, 1, ctypes.byref(sd), None):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        adv.SetFileSecurityW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_void_p]
        adv.SetFileSecurityW.restype = wintypes.BOOL
        if not adv.SetFileSecurityW(str(d), _DACL_INFO, sd):
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        _free(sd)
def _data_dir_safe(d: Path) -> bool:
    """Каталог заперт: чужих ACE на запись нет, а владелец, мы или администраторы (иначе он вернёт права)."""
    try:
        owner, _, sd = _security(d, _OWNER_INFO)
        try:
            owner_sid = _sid_str(owner)
        finally:
            _free(sd)
    except OSError:
        return False
    return (owner_sid in _OWNERS or owner_sid == _current_sid()) and not _writable_by_others(d)
def _shared_dir(p: Path) -> bool:
    """Каталог в ProgramData: его по умолчанию может создать и изменить любой пользователь."""
    pd = Path(os.environ.get("PROGRAMDATA") or "C:/ProgramData")
    return p == pd or pd in p.parents
def staging_trusted() -> bool:
    """Можно ли качать файлы обновления в DATA: папку программы или запертый ProgramData-каталог.

    Для уже установленных копий это мягкий режим: каждый запуск заново выставляет ACL, а если не вышло, программа работает, но обновление откажется ставить непроверяемый файл."""
    if not _shared_dir(DATA):  # своя папка (Program Files, портативная копия, тесты): её пишет только админ
        return True
    return DATA_LOCKED and not _suspicious(DATA) and _data_dir_safe(DATA)
def _pick_data() -> Path:
    global DATA_LOCKED
    d = ROOT / "data"
    try:
        d.mkdir(parents=True, exist_ok=True)
        t = d / ".w"
        t.write_text("1")
        t.unlink()
        return d
    except OSError:
        pass
    base = Path(os.environ.get("PROGRAMDATA") or "C:/ProgramData") / "ZapretAuto"
    base.mkdir(parents=True, exist_ok=True)
    d = base / "data"
    d.mkdir(parents=True, exist_ok=True)
    DATA_LOCKED = _lock_down(base) and _lock_down(d) and not _suspicious(d)
    return d
DATA = _pick_data()
LISTS = DATA / "lists"  # runtime lists dir passed to winws (upstream lists + user/game lists)
PRESETS = DATA / "presets"  # user strategies (general*.bat): survive zapret and app updates
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
    PRESETS.mkdir(parents=True, exist_ok=True)
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
