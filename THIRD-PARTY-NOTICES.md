# Сторонние компоненты ZapretAuto

ZapretAuto распространяется по лицензии MIT, текст в файле LICENSE. В состав поставки входят сторонние компоненты. Ниже указано, какие это компоненты, под какими лицензиями они поставляются, кому принадлежат права и где взять исходный код. Полные тексты лицензий лежат в папке LICENSES рядом с этим файлом.

Если вы получили программу без папки LICENSES или без этого файла, напишите в https://github.com/Deusu20/ZapretAuto/issues.

## Состав поставки

| Компонент | Версия | Лицензия | Правообладатель | Файлы |
|---|---|---|---|---|
| ZapretAuto | 2.2.0 | MIT | Deusu20 | ZapretAuto.exe, _internal, assets |
| zapret | 1.10.4 | MIT | bol-van | vendor\zapret\bin\winws.exe, *.bin, lists, *.bat |
| Сборка стратегий Flowseal | без номера версии | MIT | bol-van, Flowseal | vendor\zapret\*.bat, lists, utils |
| WinDivert | 2.2 | LGPLv3 (выбран этот вариант) или GPLv2 | basil00 | WinDivert.dll, WinDivert64.sys |
| Qt | 6.12.0 | LGPL-3.0-only / GPL-2.0-only / GPL-3.0-only | Qt Company | Qt6Core.dll, Qt6Gui.dll, Qt6Widgets.dll, Qt6Network.dll, Qt6Svg.dll |
| PySide6, shiboken6 | 6.12.0 | LGPL-3.0-only / GPL-2.0-only / GPL-3.0-only | Qt Company | pyside6.abi3.dll, shiboken6.abi3.dll, Shiboken.pyd, assets\qtbase_ru.qm |
| Cygwin | 3.4.10 | LGPLv3+ с Cygwin linking exception | Red Hat и участники Cygwin | cygwin1.dll |
| Python | 3.12 | PSF-2.0 | Python Software Foundation | python312.dll, стандартная библиотека |
| PyInstaller | 6.22.3 | GPLv2+ с исключением для сборки несвободных программ | участники PyInstaller | загрузчик в ZapretAuto.exe |
| curl_cffi | 0.16.3 | MIT | lexiforest | _internal\curl_cffi, _internal\curl_cffi.libs |
| libcurl | в составе curl_cffi | curl | Daniel Stenberg и участники curl | _internal\curl_cffi.libs |
| BoringSSL | в составе curl_cffi | OpenSSL License и ISC | Google и участники OpenSSL | _internal\curl_cffi.libs |
| attrs | 26.1.0 | MIT | Hynek Schlawack | _internal |
| certifi | 2026.10.23 | MPL-2.0 | Kenneth Reitz и участники | _internal |
| cffi | 2.1.1 | MIT | Armin Rigo, Maciej Fijalkowski | _internal |
| charset-normalizer | 3.5.2 | MIT | Ahmed TAHRI | _internal |
| idna | 3.20 | BSD-3-Clause | Kim Davies | _internal |
| packaging | 26.3 | Apache-2.0 или BSD-2-Clause | Donald Stufft и участники | _internal |

## zapret и сборка Flowseal

`winws.exe`, файлы `*.bin`, списки и bat-файлы взяты из проекта bol-van/zapret и сборки Flowseal/zapret-discord-youtube. Оба проекта распространяются по MIT. Требование MIT выполнено: текст лицензии и copyright включены в поставку, файлы `LICENSES/MIT-zapret.txt` и `LICENSES/MIT-flowseal.txt`.

Исходный код: https://github.com/bol-van/zapret и https://github.com/Flowseal/zapret-discord-youtube.

## WinDivert

`WinDivert.dll` и `WinDivert64.sys` поставляются без изменений. WinDivert лицензирован на выбор под LGPLv3 или GPLv2. В ZapretAuto выбран вариант LGPLv3.

Что сделано по LGPLv3 §4:

- §4(a): это уведомление сопровождает каждую копию программы и прямо сообщает, что WinDivert используется в составе ZapretAuto и покрыт LGPLv3.
- §4(b): вместе с программой поставляются тексты LGPLv3 и GPLv3: `LICENSES/LGPL-3.0.txt` и `LICENSES/GPL-3.0.txt`.
- §4(d): WinDivert поставляется отдельными DLL и SYS, а не встроен в исполняемый файл. Пользователь может заменить их на совместимую сборку. Исходный код ZapretAuto открыт, поэтому программу можно пересобрать с измененной версией библиотеки.

Исходный код WinDivert: https://github.com/basil00/WinDivert, релизы 2.2.x: https://github.com/basil00/WinDivert/releases.

Письменное предложение: в течение трех лет с момента получения экземпляра программы предоставить полный исходный код WinDivert использованной версии любому третьему лицу. Запрос можно отправить через https://github.com/Deusu20/ZapretAuto/issues.

Замена: файлы лежат в `vendor\zapret\bin`. После замены запустите проверку файлов в разделе «Диагностика».

## Qt и PySide6

Qt 6.12.0 и PySide6 6.12.0 распространяются по LGPLv3 (также доступны варианты GPL и коммерческая лицензия). В ZapretAuto выбран вариант LGPLv3.

- Уведомление: Qt и PySide6 используются в составе ZapretAuto и покрыты LGPLv3.
- Тексты LGPLv3 и GPLv3 поставляются в `LICENSES/LGPL-3.0.txt` и `LICENSES/GPL-3.0.txt`.
- Библиотеки Qt и PySide6 лежат отдельными DLL в папке `_internal`, их можно заменить на совместимую сборку той же версии.
- Исходный код Qt: https://download.qt.io/official_releases/qt/6.12/6.12.0/single/ и https://code.qt.io/cgit/qt/
- Исходный код PySide6: https://download.qt.io/official_releases/QtForPython/pyside6/ и https://code.qt.io/cgit/pyside/pyside-setup.git

Подробности и порядок замены: `LICENSES/Qt-LGPL-notes.txt`.

## Cygwin

`cygwin1.dll` версии 3.4.10 - это библиотека Cygwin API, она распространяется по LGPLv3 или более поздней версии с Cygwin linking exception. Исключение снимает требования §4 LGPLv3 для независимых модулей, но текст лицензии и исходный код библиотеки все равно передаются вместе с программой.

- Текст LGPLv3: `LICENSES/LGPL-3.0.txt`.
- Условия и текст исключения: https://cygwin.com/licensing.html, копия в `LICENSES/Cygwin-LGPL-exception.txt`.
- Исходный код: https://cygwin.com/git/ (тег cygwin-3.4.10).
- Замена: `vendor\zapret\bin\cygwin1.dll`.

## Python

Python 3.12 распространяется по PSF-2.0. Текст: `LICENSES/PSF-2.0.txt`. Исходный код: https://www.python.org/downloads/source/.

## PyInstaller

PyInstaller 6.22.3 распространяется по GPLv2 или более поздней версии со специальным исключением, которое разрешает собирать и распространять несвободные программы, в том числе коммерческие. ZapretAuto использует это исключение. Текст: `LICENSES/GPL-2.0-PyInstaller-exception.txt`. Исходный код: https://github.com/pyinstaller/pyinstaller.

## curl_cffi, libcurl, BoringSSL

curl_cffi 0.16.3 распространяется по MIT, текст: `LICENSES/MIT-curl_cffi.txt`. Внутри `curl_cffi.libs` лежат libcurl и BoringSSL. libcurl распространяется по лицензии curl (`LICENSES/curl.txt`), BoringSSL использует лицензию OpenSSL и ISC (`LICENSES/OpenSSL.txt`).

Исходный код: https://github.com/lexiforest/curl_cffi, https://curl.se, https://boringssl.googlesource.com/boringssl/.

## Python-зависимости

| Пакет | Лицензия | Текст |
|---|---|---|
| attrs | MIT | `LICENSES/MIT-attrs.txt` |
| certifi | MPL-2.0 | `LICENSES/MPL-2.0-certifi.txt` |
| cffi | MIT | `LICENSES/MIT-cffi.txt` |
| charset-normalizer | MIT | `LICENSES/MIT-charset-normalizer.txt` |
| idna | BSD-3-Clause | `LICENSES/BSD-3-Clause-idna.txt` |
| packaging | Apache-2.0 | `LICENSES/Apache-2.0-packaging.txt` |

## Контрольные суммы

SHA-256 файлов из `vendor\zapret\MANIFEST.sha256`:

| Файл | SHA-256 |
|---|---|
| cygwin1.dll | `103104a52e5293ce418944725df19e2bf81ad9269b9a120d71d39028e821499b` |
| WinDivert.dll | `c1e060ee19444a259b2162f8af0f3fe8c4428a1c6f694dce20de194ac8d7d9a2` |
| WinDivert64.sys | `8da085332782708d8767bcace5327a6ec7283c17cfb85e40b03cd2323a90ddc2` |
| winws.exe | `affb4f69d2ea302a7abccd5325d81826e140ddae014f1e070bc4a6c0dd555188` |

## Как пересобрать с другой версией библиотеки

1. Установите зависимости: `python -m venv .venv`, затем `.venv\Scripts\pip install -r requirements-dev.txt`.
2. Замените нужные DLL в `dist\ZapretAuto\_internal` (Qt, PySide6) или в `dist\ZapretAuto\vendor\zapret\bin` (WinDivert, Cygwin).
3. Пересоберите программу: `powershell -File build.ps1`.

Инструкция по сборке также есть в README, раздел «Для разработчиков».

## Контакты

Вопросы по лицензиям и исходникам: https://github.com/Deusu20/ZapretAuto/issues.
