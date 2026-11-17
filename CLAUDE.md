# ZapretAuto

Windows GUI wrapper over Flowseal/zapret-discord-youtube (winws.exe + WinDivert). Python 3.12, PySide6, psutil, PyInstaller. Runs as admin.

## Git
- Ничего не отправлять в удалённый git без явного запроса пользователя: никаких `git push`, тегов и релизов. Локальные коммиты — только если о них попросили или это часть согласованной задачи; иначе оставлять изменения в рабочем дереве и говорить об этом.

## Сборка и установка
- После каждой сборки/обновления сразу ставить новую версию на этом ПК, не дожидаясь отдельной просьбы: `build.ps1` → `dist\ZapretAuto-Setup.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /UPDATE=1`. Флаг `/UPDATE=1` обязателен: без него тихая установка не запускает приложение (в installer.iss запуск висит на `Check: IsSelfUpdate`).
- Установка и запуск приложения требуют прав администратора: `Start-Process -Verb RunAs` вызовет UAC, пользователь подтверждает. Ставить можно только когда это не мешает: установщик на несколько секунд останавливает winws.
- После установки убедиться, что приложение поднялось и обход снова работает (процесс ZapretAuto.exe, winws.exe, доступ к Discord/YouTube), и коротко сообщить, что установлено и что проверить.

## Релиз
- Тег `v*` собирает и публикует `.github/workflows/release.yml`: сборка (`contents: read`) отделена от публикации (`contents: write` + environment `release` с подтверждением). В релизе 6 файлов: `ZapretAuto-Setup.exe` и `ZapretAuto-portable.zip` с `.sha256`, `manifest.json` и `manifest.json.sig`.
- Релиз без подписанного манифеста не выпускается: приложение 2.2+ требует `manifest.json` и `.sig` в релизе, иначе отказывается обновляться.
- Приватный ключ подписи — только вне репозитория и в секрете `ZAPRETAUTO_SIGNING_KEY` **environment `release`** (не repo-level: иначе его видит любой workflow). Не копировать его в чат, коммиты и отчёты. Ключ потерян — придётся выпускать обновление с новым ключом в `signing.KEYS` (старый не удалять: им подписаны прошлые релизы).
- Порядок выпуска: поднять `__version__`, перенести раздел CHANGELOG из «Не выпущено» в `## X.Y.Z`, прогнать `build.ps1`, закоммитить и запушить `main`, поставить тег `vX.Y.Z` и запустить workflow `release` (или `gh workflow run release -f tag=vX.Y.Z`). Секрет подписи и environment: `Get-Content -Raw C:\Users\Danil\.zapretauto\signing\release.ed25519 | gh secret set ZAPRETAUTO_SIGNING_KEY --env release` (именно в environment `release` с ревьюером; repo-level секрет не использовать). Тег `v*` может создавать только владелец (ruleset `protect-release-tags`, правило `creation`), поэтому тег ставится обычным `git push --tags` от владельца. После публикации: скачать установщик, проверить, что в релизе есть `manifest.json` и `.sig`, и что установленная версия видит обновление.

## Layout
- `vendor/zapret/`: unmodified upstream release (bin/, lists/, general*.bat). Overwritten by the updater; never edit it.
- `app/`: our code. `data/`: user state (user lists, chosen strategy, game ipsets, settings.json).
- Upstream strategy = one `winws.exe` command line in `general*.bat`; profiles separated by `--new`. Vars: `%BIN%`, `%LISTS%`, `%GameFilterTCP%`, `%GameFilterUDP%`.

## Modules
- `strategies.py`: parse .bat into profiles, substitute vars, build argv; mix = pick best profile per target group.
- `runner.py`: start/stop winws.exe, single instance.
- `tester.py`: run a strategy, probe targets (TLS/HTTP + ping, like utils/targets.txt), score it.
- `updater.py`: GitHub releases/latest, replace vendor/, keep data/. App home and the only source of releases and repo JSON: github.com/Deuus20/ZapretAuto (APP_REPO). No mirrors.
- `signing.py` + `ed25519.py`: подписанный манифест релиза (`manifest.json` + `manifest.json.sig`, Ed25519 на чистом Python). Открытый ключ — в `signing.KEYS` (`k1-b7f4db33`), приватный лежит вне репозитория: `C:\Users\Danil\.zapretauto\signing\release.ed25519`, его копия — в секрете GitHub Actions `ZAPRETAUTO_SIGNING_KEY`. Подпись перед публикацией: `python tools/sign_release.py sign --key <файл> --tag vX.Y.Z` (workflow `release.yml` делает это само и падает без секрета), проверка: `... verify`. `app_update` требует оба файла и без них отказывается ставить обновление (проверка версий при этом работает).
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
- `gui/window.py`: оболочка окна (трей, жизненный цикл, `MainWindow`), собрана из миксинов: `gui/window_pages.py` (страницы и отрисовка), `gui/window_actions.py` (обход, диагностика, подбор, отчёты), `gui/window_games.py` (игры), `gui/window_updates.py` (обновления). Общие имена — `gui/shared.py`, объявления атрибутов окна для миксинов — `gui/window_base.py`, проверка «сайт не открывается» — `gui/sitecheck.py`. `main.py` entry (elevation, single instance; env ZAPRET_NO_AUTOSTART=1 skips autostart for tests).
- Settings keys (data/settings.json): strategy{base,picks}, game_tcp, game_udp, game_ports_tcp, game_ports_udp, auto_update, game_watch, game_capture, games_web_bypass, games, games_exclude, games_force, games_ignore, games_web, zapret2, quiet_updates. `game_capture` off = game servers are collected but never added to the bypass (kept in ipset-exclude-games); `games_web_bypass` = обход для браузерных игр; `games_exclude` = the same per game, the game stays listed (games page «Обход» checkbox, tray menu).
- Build: `powershell -File build.ps1` -> dist\ZapretAuto (onedir, uac-admin, vendor copied next to exe).

## Tools
- `CODEMAP.md`: all modules, classes and functions. Read it first, instead of opening files to find code; regenerate with `python tools/codemap.py` when definitions change (a test checks it).
- `python tools/make_icon.py`: regenerate assets/icon.ico from app_icon().
- `python tools/shot.py <out.png> [maxW]`: screenshot (use maxW 1024 to save tokens), then Read the png.
- `python tools/render.py <out_dir>`: render every page + states (games, busy, error, dialog) offscreen to PNGs — no window, no focus stealing, temp data dir. Prefer it over shot.py for UI work.

## Кодировки
- Вывод консольных программ (`sc`, `net`, `taskkill`, `powershell`, `ping`) — OEM-кодировка (`GetOEMCP()`, на русской Windows cp866), не UTF-8. Читать только через `diag._decode` (пробует utf-8 → cp<OEMCP> → cp1251); иначе в «Диагностике» вместо текста «������». Тест: `test_diag_console_output_in_oem_codepage`.
- Скрипты в `tools/`, чей stdout перенаправляют в PowerShell-пайп, делают `sys.stdout.reconfigure(encoding="utf-8")` — данные верные, но без этого в консоли кракозябры (`tools/sign_release.py`).
- Файлы писать с `encoding="utf-8"`: PowerShell 5.1 в `Set-Content -Encoding utf8` добавляет BOM.

## Tests
- `.venv\Scripts\python -m pytest -q` (offline: tests/conftest.py blocks sockets, curl_cffi, DNS и UDP; ~13 s); build.ps1 runs them first. Каждый тест получает временные `paths.DATA`/`paths.LISTS` (autouse-фикстура `_isolated_data`): прогон не меняет рабочую копию.
- 15 файлов по модулям: `test_strategies/test_updater/test_diag/test_gui/test_gamewatch/test_tester/test_sharedb/test_state/test_vpn/test_zapret2/test_acl/test_workflows/test_vendor_integrity/test_core.py`, общие помощники — `tests/helpers.py` (`_gui`, `_close`, `_patch_keys`, `_merge_mod`, `_games_mod`, `_body`, `_fake_pick_env`, `_Resp`, `_release_net`), враждебные входы — `test_hostile_inputs.py`. 166 тестов.
- Версии зависимостей зафиксированы: runtime — `requirements.txt`, сборка и проверки — `requirements-dev.txt` (обновление через Dependabot). `requirements.lock` — тот же runtime с хэшами (`pip-compile --generate-hashes`), CI ставит его через `--require-hashes` (и в тестах, и в релизной сборке); после смены версий в requirements.txt lock пересобрать.
- `ruff check main.py app scripts tools tests` (правила в `ruff.toml`, 0 замечаний), `pyright` — блокирующая проверка `app`, `main.py`, `tools`, `scripts` (`pyrightconfig.json`, 0 замечаний; `tests/` не входит — фикстуры pytest дают шум, а не ошибки). GUI-модули теперь тоже проверяются: Qt-вызовы переписаны на скоупленные перечисления (`Qt.AlignmentFlag.*`) и `setObjectName`/сеттеры (`q(...)`), отдельного «полного» конфига больше нет; в CI — coverage с порогом 70% (`--cov-fail-under=70`, фактически 73%).

## Token rules
- Don't read vendor/bin, big lists, service.bat or `test zapret.ps1` (denied). Grep them if needed.
- Read only the file ranges you need. Batch shell commands. Short replies.
