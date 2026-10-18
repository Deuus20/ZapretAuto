"""Press a key in a game's window (games like Wardogs wait for "press any key" before logging in)."""
import ctypes
import ctypes.wintypes as wt

import psutil

user32 = ctypes.windll.user32
WM_KEYDOWN, WM_KEYUP = 0x0100, 0x0101
VK_SPACE, VK_RETURN, VK_MENU = 0x20, 0x0D, 0x12
KEYEVENTF_KEYUP = 0x2
EnumProc = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)


def game_windows(prefix: str) -> list[int]:
    """Visible top-level windows of processes whose name starts with prefix (launchers excluded)."""
    pids = set()
    for p in psutil.process_iter(["name"]):
        n = (p.info["name"] or "").lower()
        if n.startswith(prefix.lower()) and "launcher" not in n:
            pids.add(p.pid)
    found = []

    def cb(hwnd, _):
        pid = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value in pids and user32.IsWindowVisible(hwnd) and user32.GetWindowTextLengthW(hwnd) > 0:
            found.append(hwnd)
        return True
    user32.EnumWindows(EnumProc(cb), 0)
    return found


def press_any_key(prefix: str) -> bool:
    """Bring the game window forward and press Space (+ post WM_KEYDOWN as a fallback). True if a window was found."""
    wins = game_windows(prefix)
    for hwnd in wins:
        # an ALT tap lets a background process take the foreground (SetForegroundWindow lock)
        user32.keybd_event(VK_MENU, 0, 0, 0)
        user32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)
        user32.SetForegroundWindow(hwnd)
        for vk in (VK_SPACE, VK_RETURN):
            user32.keybd_event(vk, 0, 0, 0)
            user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)
            user32.PostMessageW(hwnd, WM_KEYDOWN, vk, 0)
            user32.PostMessageW(hwnd, WM_KEYUP, vk, 0xC0000001)
    return bool(wins)
