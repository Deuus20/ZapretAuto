"""Снять все страницы окна в реальном рендере Windows: шрифты и размеры как у пользователя.

Окно не показывается (WA_DontShowOnScreen), настройки копируются во временную папку, поэтому живая
установка и работающий обход не затрагиваются. Результат: build\\screenshots\\*.png

    python tools/screenshots.py                   # все страницы
    python tools/screenshots.py --data PATH       # взять settings.json и журнал из другой папки
    python tools/screenshots.py --icons E8B1,E71C # проверить глифы шрифта иконок перед добавлением
"""
import argparse
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.pop("QT_QPA_PLATFORM", None)  # нужен настоящий windows-плагин: offscreen не видит системные шрифты

from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel  # noqa: E402

from app import paths, settings  # noqa: E402

PAGES = ("home", "games", "mix", "settings", "diag", "log", "lists")
INSTALLED_DATA = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "ZapretAuto" / "data"
OUT = ROOT / "build" / "screenshots"


def _prepare_data(src: Path | None) -> None:
    """Временная папка данных: копия настроек и журнала, чтобы экраны были как у пользователя."""
    tmp = Path(tempfile.mkdtemp(prefix="zapretauto-shots-"))
    (tmp / "lists").mkdir()
    (tmp / "presets").mkdir()
    if src is not None and (src / "settings.json").is_file():
        shutil.copy2(src / "settings.json", tmp / "settings.json")
        if (src / "logs").is_dir():
            shutil.copytree(src / "logs", tmp / "logs", dirs_exist_ok=True)
        if (src / "lists").is_dir():
            for f in (src / "lists").glob("*"):
                if f.is_file():
                    shutil.copy2(f, tmp / "lists" / f.name)
    paths.DATA = tmp
    paths.LISTS = tmp / "lists"
    paths.PRESETS = tmp / "presets"
    settings._cache = None


def _heads(window) -> None:
    """Печатает фактические размеры и отступы заголовков: расхождения видно числами, а не на глаз."""
    box = window.box_settings
    print("== заголовки в настройках: роль x pt ==")
    for lab in box.findChildren(QLabel):
        kind = lab.objectName()
        if kind in ("section", "h2", "rowTitle", "muted"):
            pos = lab.mapTo(box, QPoint(0, 0))
            print(f"  {kind:9} x={pos.x():3} pt={lab.font().pointSizeF():4.1f} | {lab.text()[:44]}")
    print("== заголовки страниц: pt ==")
    for i in range(window.tabs.count()):
        page = window.tabs.widget(i)
        for lab in page.findChildren(QLabel):
            if lab.objectName() in ("h1", "h2"):
                print(f"  {PAGES[i] if i < len(PAGES) else i:8} {lab.objectName():3} pt={lab.font().pointSizeF():4.1f}"
                      f" | {lab.text()[:40]}")


def _icons(codes: list[int]) -> None:
    """Отрисовать глифы шрифта иконок: так проверяется, что код существует, до добавления в ICONS."""
    from PySide6.QtGui import QColor, QFont, QFontDatabase, QImage, QPainter

    from app.gui.style import ICON_FONT

    font_file = next((p for p in (Path(r"C:\Windows\Fonts\SegoeIcons.ttf"),
                                  Path(r"C:\Windows\Fonts\segmdl2.ttf")) if p.is_file()), None)
    family = ICON_FONT
    if font_file is not None:  # вне приложения шрифт иконок не всегда виден QFontDatabase
        fid = QFontDatabase.addApplicationFont(str(font_file))
        fams = QFontDatabase.applicationFontFamilies(fid)
        family = fams[0] if fams else family
    cols, cw, ch = 8, 96, 96
    rows = max(1, (len(codes) + cols - 1) // cols)
    img = QImage(cols * cw, rows * ch, QImage.Format.Format_ARGB32)
    img.fill(QColor("#20242c"))
    p = QPainter(img)
    icon = QFont(family)
    icon.setPixelSize(44)
    label = QFont(family)
    label.setPixelSize(13)
    for i, cp in enumerate(codes):
        x, y = (i % cols) * cw, (i // cols) * ch
        p.setFont(icon)
        p.setPen(QColor("#e6e9f0"))
        p.drawText(x, y, cw, ch - 26, Qt.AlignmentFlag.AlignCenter, chr(cp))
        p.setFont(label)
        p.setPen(QColor("#8b93a7"))
        p.drawText(x, y + ch - 24, cw, 20, Qt.AlignmentFlag.AlignCenter, f"{cp:04X}")
    p.end()
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / "icons.png"
    img.save(str(path))
    print("icons:", path)


def main() -> int:
    ap = argparse.ArgumentParser(description="Скриншоты всех страниц ZapretAuto в реальном рендере")
    ap.add_argument("--data", type=Path, default=INSTALLED_DATA if INSTALLED_DATA.is_dir() else None,
                    help="папка data с settings.json (по умолчанию установленная программа)")
    ap.add_argument("--icons", default="", help="коды глифов через запятую, например E8B1,E71C")
    args = ap.parse_args()

    app = QApplication.instance() or QApplication([])
    if args.icons:
        codes = [int(x, 16) if not x.isdigit() else int(x, 10) for x in args.icons.split(",") if x.strip()]
        _icons(codes)
        return 0

    _prepare_data(args.data)
    from app.gui.window import MainWindow

    window = MainWindow()
    window.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)  # настоящий рендер, окно не мелькает
    window.resize(900, 620)
    window.show()
    app.processEvents()
    # Runner.stop() здесь вызывать нельзя: он делает taskkill /IM winws.exe для «чужих» winws и убил бы
    # обход запущенного приложения. Скриншоты вообще не запускают обход, поэтому останавливать нечего.
    window.set_advanced(True, save=False)
    app.processEvents()

    OUT.mkdir(parents=True, exist_ok=True)
    for i, name in enumerate(PAGES):
        window.tabs.setCurrentIndex(i)
        app.processEvents()
        window.grab().save(str(OUT / f"{i}_{name}.png"))
    window.checks_box.grab().save(str(OUT / "checks_card.png"))
    _heads(window)
    print("screenshots:", OUT)
    window.tray.hide()
    window._timer.stop()
    window.close()
    window.deleteLater()
    app.processEvents()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
