"""Look of the app: palette, stylesheet and drawn icons."""
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap

COLORS = {"on": "#22c55e", "off": "#9aa3b5", "busy": "#f59e0b"}


def app_icon(color: str = "#3b82f6") -> QIcon:
    """Shield with a power symbol; drawn at several sizes so tray and taskbar stay crisp."""
    from PySide6.QtGui import QPainterPath, QPen
    icon = QIcon()
    for size in (16, 20, 24, 32, 48, 64, 128, 256):
        pm = QPixmap(size, size)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        w = float(size)
        path = QPainterPath()
        path.moveTo(w * .5, w * .04)
        path.lineTo(w * .9, w * .18)
        path.cubicTo(w * .9, w * .62, w * .72, w * .84, w * .5, w * .97)
        path.cubicTo(w * .28, w * .84, w * .1, w * .62, w * .1, w * .18)
        path.closeSubpath()
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(color))
        p.drawPath(path)
        pen = QPen(QColor("#ffffff"), max(1.5, w * .085), Qt.SolidLine, Qt.RoundCap)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        r, cx, cy = w * .22, w * .5, w * .52  # float geometry: int() rounding shifted it left at 16-32 px
        p.drawArc(QRectF(cx - r, cy - r, 2 * r, 2 * r), 125 * 16, 290 * 16)
        p.drawLine(QPointF(cx, cy - r * 1.18), QPointF(cx, cy - r * .05))
        p.end()
        icon.addPixmap(pm)
    return icon


def power_icon(color: str, size: int = 44) -> QIcon:
    """Classic power symbol: open arc + vertical bar."""
    from PySide6.QtGui import QPen
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    pen = QPen(QColor(color), size * 0.1, Qt.SolidLine, Qt.RoundCap)
    p.setPen(pen)
    m = size * 0.16
    p.drawArc(int(m), int(m), int(size - 2 * m), int(size - 2 * m), 120 * 16, 300 * 16)
    p.drawLine(int(size / 2), int(size * 0.08), int(size / 2), int(size * 0.45))
    p.end()
    return QIcon(pm)


STYLE = """
QWidget { background: #15171c; color: #e6e8ee; font-family: "Segoe UI"; font-size: 10pt; }
QFrame#card { background: #1e2129; border-radius: 14px; }
QLabel#title { font-size: 17pt; font-weight: 600; background: transparent; }
QLabel#sub { color: #b4bccb; background: transparent; }
QPushButton { background: #262a34; border: 1px solid #323744; border-radius: 8px; padding: 7px 14px; }
QPushButton:hover { background: #2e3340; }
QPushButton:disabled { color: #8b93a3; }
QPushButton:focus, QCheckBox:focus, QComboBox:focus { outline: none; border: 1px solid #3b82f6; }
QPushButton#link { background: transparent; border: none; color: #60a5fa; padding: 2px 0; }
QPushButton#link:hover { color: #93c5fd; }
QPushButton#primary { background: #3b82f6; border: none; color: white; font-weight: 600; }
QPushButton#primary:hover { background: #4b8ff8; }
QPushButton#primary:disabled { background: #2a3550; color: #8090b0; }
QPushButton#power { border-radius: 48px; font-size: 34pt; border: 3px solid #4b5263; background: #262a34; color: #9aa3b5; }
QPushButton#power[state="on"] { border-color: #22c55e; color: #22c55e; background: #16261d; }
QPushButton#power[state="busy"] { border-color: #f59e0b; color: #f59e0b; background: #2a2314; }
QGroupBox { border: 1px solid #2a2f3a; border-radius: 10px; margin-top: 10px; padding: 12px 10px 8px 10px; }
QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 4px; color: #9aa3b5; }
QCheckBox { spacing: 8px; background: transparent; padding: 3px 0; }
QCheckBox::indicator { width: 16px; height: 16px; border: 2px solid #4b5263; border-radius: 5px; background: #1b1e25; }
QCheckBox::indicator:hover { border-color: #3b82f6; }
QCheckBox::indicator:checked { background: #3b82f6; border-color: #3b82f6; }
QComboBox { background: #262a34; border: 1px solid #323744; border-radius: 8px; padding: 6px 10px; }
QComboBox::drop-down { border: none; width: 26px; }
QComboBox QAbstractItemView { background: #1e2129; selection-background-color: #3b82f6; }
QTabWidget::pane { border: 1px solid #2a2f3a; border-radius: 8px; top: -1px; }
QTabBar::tab { background: transparent; padding: 7px 14px; color: #9aa3b5; border-bottom: 2px solid transparent; }
QTabBar::tab:selected { color: #e6e8ee; border-bottom: 2px solid #3b82f6; }
QPlainTextEdit, QTableWidget, QListWidget { background: #1b1e25; border: none; border-radius: 8px; }
QPlainTextEdit { font-family: Consolas; }
QTableWidget { gridline-color: #262a34; }
QHeaderView::section { background: #1e2129; color: #9aa3b5; border: none; padding: 5px; }
QWidget#chipsrow { background: transparent; }
QLabel#chip { border-radius: 10px; padding: 2px 9px; font-size: 9pt; }
QLabel#chip[ok="yes"] { background: #14301f; color: #4ade80; }
QLabel#chip[ok="no"] { background: #3a1a1a; color: #f87171; }
QLabel#empty { color: #8b93a3; padding: 30px; background: transparent; }
QProgressBar#thin { background: #1e2129; border: none; border-radius: 2px; }
QProgressBar#thin::chunk { background: #3b82f6; border-radius: 2px; }
QToolTip { background: #262a34; color: #e6e8ee; border: 1px solid #3b82f6; padding: 6px; }
"""
