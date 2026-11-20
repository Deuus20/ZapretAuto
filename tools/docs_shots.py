"""Скриншоты для docs/img и README: страницы окна в реальном рендере Windows.

Снимок 900x630 уменьшается до 800x560 — так же сделаны прежние картинки в README и руководстве.
Данные демонстрационные (временная папка данных), живая установка и обход не затрагиваются, winws не запускается.

    python tools/docs_shots.py

Картинки перезаписываются в docs/img: main-on, autopick, settings, games, diagnostics, log.
"""
import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.pop("QT_QPA_PLATFORM", None)  # нужен настоящий windows-плагин: offscreen не видит системные шрифты

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QLayout, QWidget  # noqa: E402

from app import __version__, paths, settings  # noqa: E402

OUT = ROOT / "docs" / "img"
SHOT = (900, 630)  # реальный размер окна при съёмке
SIZE = (800, 560)  # размер картинок в README и руководстве (900x630 = 10:7)

# демонстрационные игры: две записи, как в прежних картинках (Wardogs играете, Deadlock ждёт)
GAMES = {
    "wardogs.exe": {"ips": [f"203.0.113.{i}" for i in range(1, 111)], "ping_ms": 38, "active": True, "path": ""},
    "deadlock.exe": {"ips": [f"198.51.100.{i}" for i in range(1, 57)], "ping_ms": None, "active": False, "path": ""},
}
LOGS = (
    f"start ZapretAuto {__version__}, Windows 10.0.19045",
    "запущено: ALT8",
    "проверка доступа: Discord ✓ YouTube ✓ Голос Discord ✓",
    "найдена игра: Wardogs",
    "найдена игра: Deadlock",
    "IP-список обновлён: 186 подсетей",
)
# диагностика: две находки с кнопкой «Исправить» и три спокойных строки — как в руководстве
DIAG = (
    {"label": "DNS и DoH", "level": "warn", "fix": "dns_repair",
     "detail": "провайдер подменяет адреса: youtube.com — обход не поможет без защищённого DNS. «Исправить» "
               "включит его в Chrome, Edge и Яндекс.Браузере"},
    {"label": "QUIC в браузерах", "level": "warn", "fix": "disable_quic",
     "detail": "QUIC до YouTube не проходит — «Исправить» отключит QUIC в браузерах, видео пойдёт по TCP"},
    {"label": "Base Filtering Engine", "level": "ok", "detail": "Running"},
    {"label": "Файлы обхода", "level": "ok", "detail": "winws.exe, WinDivert на месте"},
    {"label": "Кэш Discord", "level": "ok", "fix": "discord_cache",
     "detail": "если Discord висит на загрузке — «Исправить» очистит кэш, вход сохранится"},
)


class _Runner:
    """Состояние обхода без winws: снимкам не нужен ни запуск, ни остановка."""

    def __init__(self, running: bool = False) -> None:
        self.running = running

    def start(self, *args, **kwargs) -> None:
        self.running = True

    def stop(self) -> None:
        self.running = False


class _Watcher:
    """Найденные игры: карточка на главной и таблица на странице «Игры»."""

    def games(self) -> dict:
        return GAMES


def _stats() -> list[dict]:
    """Неделя событий для статистики в журнале: работа, проверки, починки."""
    now = time.time()
    events: list[dict] = []
    for day in range(1, 8):
        base = now - day * 86400
        events.append({"t": base - 5 * 3600, "k": "start"})
        events.append({"t": base - 2.5 * 3600, "k": "stop"})
        for i in range(40):
            events.append({"t": base - 4 * 3600 + i * 300, "k": "check", "ok": not (day == 3 and i in (3, 7))})
    events += [
        {"t": now - 3 * 86400, "k": "repick"},
        {"t": now - 1 * 86400, "k": "repick"},
        {"t": now - 4 * 3600, "k": "switch", "to": "ALT8"},
        {"t": now - 6 * 86400, "k": "ipset"},
        {"t": now - 2 * 86400, "k": "crash"},
    ]
    return sorted(events, key=lambda e: e["t"])


def _prepare_data() -> None:
    """Демонстрационные настройки, статистика и журнал во временной папке."""
    tmp = Path(tempfile.mkdtemp(prefix="zapretauto-docs-"))
    (tmp / "lists").mkdir()
    (tmp / "presets").mkdir()
    paths.DATA, paths.LISTS, paths.PRESETS = tmp, tmp / "lists", tmp / "presets"
    settings._cache = None
    settings.save_settings(
        strategy={"base": "ALT8", "picks": {}},
        auto_choice={"base": "ALT9", "picks": {}},  # подбор остановился на ALT9, стратегию выбрали вручную
        game_watch=True,
        game_capture=True,
        show_advanced=True,
        ipset_updated=time.time(),
    )
    (tmp / "stats.json").write_text(json.dumps(_stats()), encoding="utf-8")


def _activate(page: QWidget) -> None:
    """Пересчитать раскладку страницы: без этого подпись, которой текст задали до показа, остаётся высотой в строку."""
    lay = page.layout()
    if isinstance(lay, QLayout):
        lay.activate()


def _settle(window: QWidget, page: QWidget, *labels: QWidget) -> None:
    """Дать раскладке пересчитать высоту подписей с текстом, заданным пока страница была не на виду."""
    app = QApplication.instance()
    for label in labels:
        label.updateGeometry()
    if app is not None:
        app.processEvents()
    _activate(page)
    if app is not None:
        app.processEvents()


def _save(window: QWidget, name: str) -> None:
    img = window.grab()
    if (img.width(), img.height()) != SIZE:
        img = img.scaled(*SIZE, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
    img.save(str(OUT / f"{name}.png"))
    print(f"{name}: {img.width()}x{img.height()}, {(OUT / f'{name}.png').stat().st_size} B")


def main() -> int:
    _prepare_data()
    app = QApplication.instance() or QApplication([])
    from app.gui.window import MainWindow

    window = MainWindow()
    window.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)  # настоящий рендер, окно не мелькает
    window.resize(*SHOT)
    window.show()
    app.processEvents()
    window._timer.stop()  # снимки не должны ждать фоновых проверок и обновлений
    window.runner = _Runner(True)
    window.watcher = _Watcher()

    # главная: обход включён, все сервисы работают, две игры найдены
    window.refresh()
    window.set_chips({"Discord": True, "YouTube": True, "Голос Discord": True})
    window.fill_games()
    for line in LOGS:
        window.log(line)
    window.tabs.setCurrentIndex(0)
    _settle(window, window.tabs.widget(0), window.lbl_recent)
    _save(window, "main-on")

    # подбор стратегии: идёт проверка, часть сервисов ещё не открывается
    window.busy, window.busy_text = True, "Подбираю стратегию…"
    window._cancellable = True
    window.refresh()
    window.substatus.setText("Проверено стратегий: 7 из 21, осталось около 2 мин")
    window.set_chips({"Discord": True, "YouTube": True, "Голос Discord": False})
    window.progress.setRange(0, 25)
    window.progress.setValue(7)
    app.processEvents()
    _save(window, "autopick")

    window.busy, window.busy_text, window._cancellable = False, "", False
    window.refresh()
    app.processEvents()

    # игры: таблица с двумя найденными играми, строка играющей выбрана
    window.tabs.setCurrentIndex(1)
    window.fill_games()
    app.processEvents()
    window.table.selectRow(0)
    app.processEvents()
    _save(window, "games")

    # настройки: основное и дополнительно, сверху страницы
    window.tabs.setCurrentIndex(3)
    window.set_advanced(True, save=False)
    _activate(window.tabs.widget(3))
    app.processEvents()
    _save(window, "settings")

    # диагностика: две находки с «Исправить» и спокойные проверки
    window.tabs.setCurrentIndex(4)
    window.on_diag_done([dict(r) for r in DIAG])
    _activate(window.tabs.widget(4))
    app.processEvents()
    _save(window, "diagnostics")

    # журнал: статистика за неделю и записи
    window.tabs.setCurrentIndex(5)
    window.fill_stats()
    _settle(window, window.tabs.widget(5), window.lbl_stats)
    _save(window, "log")

    window.tray.hide()
    window._timer.stop()
    window.close()
    window.deleteLater()
    app.processEvents()
    print("готово:", OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
