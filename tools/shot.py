"""Screenshot all screens, downscaled to save tokens. Usage: python tools/shot.py [out.png] [max_width]"""
import sys
from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
out = sys.argv[1] if len(sys.argv) > 1 else "shot.png"
max_w = int(sys.argv[2]) if len(sys.argv) > 2 else 1280
app = QGuiApplication(sys.argv)
scr = QGuiApplication.primaryScreen()
g = scr.virtualGeometry()
img = scr.grabWindow(0, g.x(), g.y(), g.width(), g.height())
if img.width() > max_w:
    img = img.scaledToWidth(max_w, Qt.SmoothTransformation)
img.save(out)
print(out, img.width(), img.height())
