"""Is the user in the middle of something an update would break: a call or a full-screen app (game, film, slides)?
Used by the «Обновляться только в тишине» setting; an update restarts winws and drops connections for a minute."""
import ctypes

import psutil

CALL_APPS = {"discord.exe", "discordptb.exe", "discordcanary.exe", "telegram.exe", "zoom.exe", "teams.exe",
             "ms-teams.exe", "skype.exe", "whatsapp.exe", "viber.exe"}
# idle background UDP of these apps: mDNS, SSDP, NetBIOS, DHCP — not a call
_IDLE_PORTS = {53, 67, 68, 137, 138, 1900, 5353, 5355}


def fullscreen() -> bool:
    """Windows itself says «don't disturb»: a full-screen game (D3D), a full-screen app or presentation mode."""
    state = ctypes.c_int(0)
    try:
        if ctypes.windll.shell32.SHQueryUserNotificationState(ctypes.byref(state)) != 0:
            return False
    except (AttributeError, OSError):
        return False
    return state.value in (2, 3, 4)  # QUNS_BUSY, QUNS_RUNNING_D3D_FULL_SCREEN, QUNS_PRESENTATION_MODE


def call_app(conns=None) -> str | None:
    """Name of a messenger holding a media (UDP) socket — voice/video runs over UDP; None when there is none."""
    try:
        conns = psutil.net_connections(kind="udp") if conns is None else conns
    except (psutil.Error, OSError):
        return None
    names = {}
    for c in conns:
        if not c.pid or not c.laddr or c.laddr.port in _IDLE_PORTS or c.laddr.ip in ("127.0.0.1", "::1"):
            continue
        if c.pid not in names:
            try:
                names[c.pid] = psutil.Process(c.pid).name().lower()
            except psutil.Error:
                names[c.pid] = ""
        if names[c.pid] in CALL_APPS:
            return names[c.pid].rsplit(".", 1)[0]
    return None


def busy_reason(game: bool = False) -> str | None:
    """Why now is a bad time to update, in words for the log; None when it is quiet."""
    if game:
        return "идёт игра"
    app = call_app()
    if app:
        return f"идёт звонок ({app})"
    if fullscreen():
        return "открыто полноэкранное приложение"
    return None
