"""Render assets/icon.ico (exe + installer icon) from app_icon(). Usage: python tools/make_icon.py"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication(sys.argv)
from PIL import Image  # noqa: E402

from app.gui.window import app_icon  # noqa: E402

png = ROOT / "assets" / "icon-256.png"
app_icon("#3b82f6").pixmap(256, 256).save(str(png))
Image.open(png).save(ROOT / "assets" / "icon.ico", sizes=[(s, s) for s in (16, 20, 24, 32, 48, 64, 128, 256)])
print("assets/icon.ico")
