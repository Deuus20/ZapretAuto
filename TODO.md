# TODO

## Ждёт вашего решения
- Почта `yuav322@gmail.com` в истории связывает новый аккаунт со старым. Заменить можно только перезаписью истории на GitHub.
- Через несколько недель: заявка на подпись кода в SignPath и добавление в winget, оба от аккаунта `Deuus20`.

## Состояние на 17.11.2026 (после аудита v5 и независимой проверки)
Отчёт: `C:\Users\Danil\Documents\deepseek-harness\default-workspace\ZapretAuto-Review-v6.docx`;
протокол независимой проверки - `ZapretAuto-verification.md` в той же папке.

### Сделано
- **Подпись релиза:** `app/ed25519.py` (чистый Python, RFC 8032 + отсев точек малого порядка), `app/signing.py`,
  `tools/sign_release.py`; `app_update` ставит установщик только после проверки `manifest.json`/`.sig`.
  Ключ `k1-b7f4db33`; приватный - `C:\Users\Danil\.zapretauto\signing\release.ed25519` (сделать резервную копию!),
  копия - в секрете `ZAPRETAUTO_SIGNING_KEY`. `verify` требует все 4 файла релиза и проверяет repo по константе.
- **CI/workflow:** `tests.yml` - валидный YAML (проверяется тестом `test_workflows.py`), permissions, actions по SHA,
  `persist-credentials: false`, runtime из `requirements.lock` через `--require-hashes` (и в тестах, и в релизе),
  pyright-блокировка, coverage ≥ 70%; `release.yml` - сборка и публикация раздельно, environment `release`, шаг подписи
  падает без секрета; CodeQL, Dependabot, pip-audit, SBOM, SECURITY.md, CODEOWNERS.
- **GitHub через API (17.11.2026):** branch protection `main` (обязательный `pytest`, запрет force-push и удаления),
  ruleset `protect-release-tags` для `v*`, environment `release` (ревьюер Deusu20), secret scanning + push protection,
  Dependabot alerts и автоисправления, секрет подписи.
- **Руками (17.11.2026):** 2FA на `Deuus20`; токен Codeberg отозван, переменная `CODEBERG_TOKEN` удалена.
- **P0 из проверки:** `tests.yml` больше не ломается на `name:` с двоеточием; архив zapret без digest не ставится;
  каталог данных в ProgramData запирается фактическим DACL (SetFileSecurity) с проверкой владельца и ACE, а не
  `icacls /grant:r`; из отчёта и issue вычищаются IPv6 и профили на любом диске.
- **P1/P2 из проверки:** атомарная запись hosts с `.bak`; `tester.BIND` сбрасывается после проверки сайта; манифест
  vendor ловит лишние файлы, а удалённый после первой проверки манифест это расхождение; тесты не пишут в рабочую
  копию (`_isolated_data`), conftest блокирует UDP/DNS; merge-боты не падают на повторном отчёте и закрывают issue
  `always()`; метки GUI не разбирают HTML из данных; валидация ipset через `ipaddress`; `logs.setup()` идемпотентен;
  копия WinDivert сверяется по sha256; портативное обновление хранит `.old` и откатывается при обрыве;
  `merge_report` считает голоса по авторам (≥ 2 авторов, ≤ 5 отчётов на автора).

### Осталось
- **Выпуск 2.2.0 - подготовлен локально 17.11.2026:** `__version__` = 2.2.0, раздел CHANGELOG переименован в
  `## 2.2.0-2026-11-17`, exe/portable/установщик пересобраны и установлены, `manifest.json` подписан и проверен
  для v2.2.0. Осталось только отправить: коммит + `git push`, тег `v2.2.0`, workflow `release` (публикация ждёт
  подтверждения в environment `release`). Делать только по явной команде: push/теги/релиз без неё не выполнять.
- **GitHub-настройки - сделано 17.11.2026 (пункты 1 и 3):** секрет подписи перенесён в environment `release`
  (repo-level удалён); в ruleset `protect-release-tags` добавлено правило `creation`, обход только у владельца
  Deusu20 - остальные не могут создавать теги `v*`.
- **GitHub-настройки - не включено осознанно:** required PR review / code-owner review. Владелец один и админ
  обходит такую защиту, а обязательный PR добавляет шаг в работу; включить, если появится второй человек с доступом.
- **GUI-долг - закрыт 17.11.2026:** Qt-вызовы переписаны на скоупленные перечисления (`Qt.AlignmentFlag.*` и др.)
  и сеттеры (`q(...)` вместо свойств в конструкторе); `window.py` разбит на оболочку (~300 строк) и миксины
  `window_pages.py` / `window_actions.py` / `window_games.py` / `window_updates.py` (+ `shared.py`, `window_base.py`,
  `sitecheck.py`). Блокирующий pyright теперь проверяет и `app/gui`, и `tools`, и `scripts` - 0 замечаний,
  `pyrightconfig.full.json` удалён, отдельного «полного» прогона в CI больше нет.
- **PySide6-Essentials - сделано 17.11.2026:** runtime ставит `PySide6-Essentials==6.12.0` вместо метапакета
  (без Addons/WebEngine/Pdf), `requirements.lock` пересобран с хэшами, тесты и сборка с ним проходят.
  `requirements-dev.txt` дополнен `pip-tools`, `pyyaml` и `pyinstaller-hooks-contrib`.

## Позже
- Производительность GUI (не блокирует релиз): `fill_games` каждые 2 с пересобирает всю таблицу - перейти на diff по строкам (exe/ping/active/ips), чтобы не мигало и не жгло CPU; `refresh()` каждые 2 с заново выставляет `setToolTip`/`setEnabled`/`currentIndex` и дважды зовёт `describe()` - кэшировать последнее состояние. Диагностику (3 запуска PowerShell, ~1,1 с) можно свести к одному JSON-скрипту.
- Shift+Tab в Steam (жалобы пользователей): в 2.1.1 сети Valve убраны из TCP-обхода, но это гипотеза, на реальном случае не проверена. Если жалобы останутся, спросить: какая игра, возвращается ли оверлей при паузе обхода, одинаковые ли права у Steam и игры.
- В ipset-games остаются старые записи 1.1.1.1 и 1.0.0.1 (DNS Cloudflare): их нет в CDN_NETS, стоит добавить туда DNS-резолверы.
- Ревью, не сделано: /24-группировка игровых IP тянет чужие хосты (оставлена намеренно: помогает ловить серверы игры); базовый замер пинга может идти уже через обход.
- Запуск/остановка winws в потоке GUI: обычно ~0,3 с (замерено), подвисание на 5-15 с только если winws не завершается сразу (`Runner.stop`: `p.wait(5)`, `taskkill`). Низкий приоритет: перенести этот случай в Worker.
