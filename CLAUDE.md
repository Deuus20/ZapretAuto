# ZapretAuto

Windows GUI wrapper over Flowseal/zapret-discord-youtube (winws.exe + WinDivert). Python 3.12, PySide6, psutil, PyInstaller. Runs as admin.

## Layout
- `vendor/zapret/`: unmodified upstream release (bin/, lists/, general*.bat). Overwritten by the updater; never edit it.
- `app/`: our code. `data/`: user state (user lists, chosen strategy, game ipsets, settings.json).
- Upstream strategy = one `winws.exe` command line in `general*.bat`; profiles separated by `--new`. Vars: `%BIN%`, `%LISTS%`, `%GameFilterTCP%`, `%GameFilterUDP%`.

## Modules
- `strategies.py`: parse .bat into profiles, substitute vars, build argv; mix = pick best profile per target group.
- `runner.py`: start/stop winws.exe, single instance.
- `tester.py`: run a strategy, probe targets (TLS/HTTP + ping, like utils/targets.txt), score it.
- `updater.py`: GitHub releases/latest, replace vendor/, keep data/.
- `gamewatch.py`: psutil poll for new processes with remote endpoints; probe; add IPs to data ipset + game filter.
- `diag.py`: system checks + fixes. `lists.py`: ipset update from GitHub (marker ipset-all.txt.custom), user list paths.
- `gui/window.py`: tray + main window; `main.py` entry (elevation, single instance; env ZAPRET_NO_AUTOSTART=1 skips autostart for tests).
- Settings keys (data/settings.json): strategy{base,picks}, game_tcp, game_udp, auto_update, game_watch, games, games_force, games_ignore.
- Build: `powershell -File build.ps1` -> dist\ZapretAuto (onedir, uac-admin, vendor copied next to exe).

## Tools
- `python tools/shot.py <out.png> [maxW]`: screenshot (use maxW 1024 to save tokens), then Read the png.

## Token rules
- Don't read vendor/bin, big lists, service.bat or `test zapret.ps1` (denied). Grep them if needed.
- Read only the file ranges you need. Batch shell commands. Short replies.
