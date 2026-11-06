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
- `updater.py`: GitHub releases/latest, replace vendor/, keep data/. App home: github.com/Deuus20/ZapretAuto (APP_REPO). App release + repo JSON fall back to the Codeberg mirror (MIRROR_REPO), frozen at 2.1.1: no longer published, by the user's decision.
- `gamewatch.py`: psutil poll for new processes with remote endpoints; probe; add IPs to data ipset + game filter.
- `settings.py`: settings.json with lock, atomic write, mtime cache (tester re-exports load/save).
- `vpn.py`: VPN server IPs -> ipset-exclude-vpn.txt (all profiles), bind_ip() to test around full-tunnel VPN.
- `sharedb.py`: shared per-ISP (ASN) picks: strategies-db.json in repo, reports via issues merged by .github/workflows/strategy-db.yml + scripts/merge_report.py.
- Shared game servers: games-db.json (published = sent by 2+ people), «Поделиться адресами…» issue `[games]` merged by .github/workflows/games-db.yml + scripts/merge_games.py; gamewatch loads it at start (cache data/games-db.json).
- `zapret2.py`: experimental zapret2 mode (setting `zapret2`): winws2 + Lua `circular` rotates 5 ported strategies per site; files from bol-van/zapret-win-bundle at a pinned commit, sha256-checked, into data/zapret2 (the zapret2 release zip trips Defender reputation: never download it).
- `quiet.py`: call (messenger UDP socket) / full-screen detection for the `quiet_updates` setting. `stats.py`: event history; `daily()` feeds the diagnostics report (logs.py).
- `diag.py`: system checks + fixes. `lists.py`: ipset update from GitHub (marker ipset-all.txt.custom), user list paths.
- `ipset-exclude-steam.txt` (`paths.VALVE_NETS`, rewritten by `sync_lists`): excluded from TCP ipset profiles so Steam's own client link is not desynced; UDP keeps the bypass for SDR games.
- `gui/style.py`: Material 3 dark tokens `C`, stylesheet, icon font (Segoe Fluent/MDL2 glyphs), app icon. `gui/widgets.py`: Switch, SettingRow, Rail + Pages (QTabWidget-like API: tabs.addTab/setTabVisible), PowerButton, StatusChip. `gui/worker.py`: Python-thread Worker + Bridge signals.
- `gui/window.py`: tray + main window; `main.py` entry (elevation, single instance; env ZAPRET_NO_AUTOSTART=1 skips autostart for tests).
- Settings keys (data/settings.json): strategy{base,picks}, game_tcp, game_udp, game_ports_tcp, game_ports_udp, auto_update, game_watch, games, games_force, games_ignore, games_web, zapret2, quiet_updates.
- Build: `powershell -File build.ps1` -> dist\ZapretAuto (onedir, uac-admin, vendor copied next to exe).

## Tools
- `CODEMAP.md`: all modules, classes and functions. Read it first, instead of opening files to find code; regenerate with `python tools/codemap.py` when definitions change (a test checks it).
- `python tools/make_icon.py`: regenerate assets/icon.ico from app_icon().
- `python tools/shot.py <out.png> [maxW]`: screenshot (use maxW 1024 to save tokens), then Read the png.
- `python tools/render.py <out_dir>`: render every page + states (games, busy, error, dialog) offscreen to PNGs — no window, no focus stealing, temp data dir. Prefer it over shot.py for UI work.

## Tests
- `.venv\Scripts\python -m pytest -q` (offline: tests/conftest.py blocks sockets; ~6 s); build.ps1 runs them first.

## Token rules
- Don't read vendor/bin, big lists, service.bat or `test zapret.ps1` (denied). Grep them if needed.
- Read only the file ranges you need. Batch shell commands. Short replies.
