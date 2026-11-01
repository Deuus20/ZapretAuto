# TODO

## Нужно ваше решение
- Выпустить 2.0.2 (много исправлений, см. CHANGELOG «Не выпущено»): GitHub-репо всё ещё скрыт (404), поэтому релиз только на Codeberg через `tools/codeberg_publish.py`, затем установить на ПК. Выпускать?

## Позже
- Выпуск 2.1.0 (ночная сессия 2026-11-02 прервана лимитом): bump app/__init__.py → 2.1.0, заголовок CHANGELOG «## 2.1.0 — дата», build.ps1, --selftest, commit+tag, tools/codeberg_publish.py v2.1.0 (GitHub 404), установка на ПК.
- Непроверенные находки ревью: gamewatch классифицирует трафик игры на любом порту (53/123/8443) → ipset-games; /24-группировка тянет чужие хосты; vpn.py ищет VPN по подстроке ("throne" ловит ThroneAndLiberty); тестовые прогоны пишут в list-auto.txt; ipset-telegram попадает в игровой UDP-профиль; звонилки (Signal, WhatsApp.Root, discordptb/canary, Vesktop, AyuGram) могут считаться играми; бан IP при откате без срока.
- Запуск/остановка winws (`start_runner` → `Runner.stop` с `p.wait(5)` и `taskkill`) идёт в потоке GUI: при перезапуске после падения или смене VPN окно и трей могут подвиснуть на 5–15 с. Перенести в Worker с состоянием «занят».
