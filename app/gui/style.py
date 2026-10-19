"""Look of the app: Material 3 dark tokens, stylesheet, icon font and the drawn app icon."""
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap

# Material 3 dark scheme from a blue seed (primary tone 80, containers 30, surfaces 6–22), plus status roles
C = {
    "surface": "#111318", "surface_low": "#191c20", "surface_cont": "#1d2024", "surface_high": "#272a2f",
    "surface_highest": "#32353a", "on_surface": "#e2e2e9", "on_surface_variant": "#c3c6cf",
    "outline": "#8d9199", "outline_variant": "#43474e",
    "primary": "#a8c7fa", "on_primary": "#062e6f", "primary_container": "#0842a0",
    "on_primary_container": "#d3e3fd", "secondary_container": "#3c4758", "on_secondary_container": "#d8e2f6",
    "good": "#6dd58c", "good_container": "#0f3d1f", "on_good_container": "#9df2b0",
    "warn": "#f9bc4d", "warn_container": "#3f2e00",
    "error": "#ffb4ab", "error_container": "#601410", "on_error_container": "#ffdad6",
}
DARK = dict(C)
# the same roles from the light scheme (primary tone 40, containers 90, surfaces 98–90)
LIGHT = {
    "surface": "#f9f9ff", "surface_low": "#f2f3fa", "surface_cont": "#ededf4", "surface_high": "#e7e8ee",
    "surface_highest": "#e2e2e9", "on_surface": "#191c20", "on_surface_variant": "#44474e",
    "outline": "#74777f", "outline_variant": "#c4c6d0",
    "primary": "#415f91", "on_primary": "#ffffff", "primary_container": "#d6e3ff",
    "on_primary_container": "#001b3e", "secondary_container": "#dae2f9", "on_secondary_container": "#131c2b",
    "good": "#1b6d32", "good_container": "#c5f0cd", "on_good_container": "#00210a",
    "warn": "#7a5900", "warn_container": "#ffdea4",
    "error": "#ba1a1a", "error_container": "#ffdad6", "on_error_container": "#410002",
}
COLORS = {"on": "#22c55e", "off": "#9aa3b5", "busy": "#f59e0b"}  # tray/taskbar icon fills: small, need punch


def windows_light() -> bool:
    """Windows «apps» theme: light (True) or dark."""
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as k:
            return winreg.QueryValueEx(k, "AppsUseLightTheme")[0] == 1
    except OSError:
        return False


def set_theme(light: bool) -> bool:
    """Switch the tokens in place (widgets read C when they paint). True if it changed."""
    new = LIGHT if light else DARK
    if C == new:
        return False
    C.clear()
    C.update(new)
    return True

# Segoe Fluent Icons (Win11) and Segoe MDL2 Assets (Win10) share these code points
ICONS = {"home": "", "games": "", "settings": "", "diag": "", "log": "",
         "lists": ""}
ICON_FONT = "Segoe MDL2 Assets"


def glyph(name: str) -> str:
    return ICONS.get(name, "")


def init_fonts() -> None:
    """Pick the icon font once the QApplication exists."""
    global ICON_FONT
    from PySide6.QtGui import QFontDatabase
    fams = set(QFontDatabase.families())
    ICON_FONT = next((f for f in ("Segoe Fluent Icons", "Segoe MDL2 Assets") if f in fams), ICON_FONT)
    from . import widgets
    widgets.ICON_FONT = ICON_FONT


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


def _rgba(color: str, a: float) -> str:
    c = QColor(color)
    return f"rgba({c.red()}, {c.green()}, {c.blue()}, {a})"


def _mix(base: str, layer: str, t: float) -> str:
    """M3 state layer: `layer` at opacity t over `base`, as an opaque color."""
    b, l_ = QColor(base), QColor(layer)
    return QColor(round(b.red() + (l_.red() - b.red()) * t), round(b.green() + (l_.green() - b.green()) * t),
                  round(b.blue() + (l_.blue() - b.blue()) * t)).name()


def build_style() -> str:
    """The app stylesheet from the current tokens C (rebuilt when the Windows theme changes)."""
    hover, press = _rgba(C["primary"], .08), _rgba(C["primary"], .12)
    off_fg, off_bg = _rgba(C["on_surface"], .38), _rgba(C["on_surface"], .12)
    primary_hover = _mix(C["primary"], C["on_primary"], .08)
    tonal_hover = _mix(C["secondary_container"], C["on_secondary_container"], .08)
    menu_hover = _rgba(C["on_surface"], .08)
    return f"""
* {{ font-family: "Segoe UI Variable Text", "Segoe UI"; font-size: 9.5pt; color: {C['on_surface']}; }}
QWidget {{ background: transparent; }}
QWidget#root, QWidget#rail, QStackedWidget, QWidget#page {{ background: {C['surface']}; }}
QScrollArea {{ background: {C['surface']}; border: none; }}
QScrollArea > QWidget > QWidget {{ background: {C['surface']}; }}

QLabel#h1 {{ font-size: 14pt; font-weight: 600; }}
QLabel#title {{ font-size: 15pt; font-weight: 600; }}
QLabel#h2 {{ font-size: 10.5pt; font-weight: 600; }}
QLabel#sub, QLabel#muted {{ color: {C['on_surface_variant']}; }}
QLabel#sub[error="yes"] {{ color: {C['error']}; }}
QLabel#rowTitle {{ font-size: 10pt; }}
QLabel#rowTitle:disabled {{ color: {C['outline']}; }}
QLabel#section {{ color: {C['primary']}; font-weight: 600; font-size: 9pt; padding: 10px 12px 4px 12px; }}
QLabel#empty {{ color: {C['on_surface_variant']}; padding: 24px; font-size: 10pt; }}

QFrame#card, QFrame#group {{ background: {C['surface_low']}; border-radius: 12px; }}
QWidget#row {{ border-radius: 8px; }}
QWidget#row:hover {{ background: {C['surface_cont']}; }}

QPushButton {{ border-radius: 15px; padding: 5px 14px; font-weight: 600; min-height: 20px;
              background: transparent; border: 1px solid {C['outline']}; color: {C['primary']}; }}
QPushButton:hover {{ background: {hover}; }}
QPushButton:pressed {{ background: {press}; }}
QPushButton:focus {{ border: 2px solid {C['primary']}; }}
QPushButton:disabled {{ color: {off_fg}; border-color: {off_bg}; }}
QPushButton#primary {{ background: {C['primary']}; color: {C['on_primary']}; border: none; }}
QPushButton#primary:hover {{ background: {primary_hover}; }}
QPushButton#primary:disabled, QPushButton#tonal:disabled {{ background: {off_bg}; color: {off_fg}; }}
QPushButton#tonal {{ background: {C['secondary_container']}; color: {C['on_secondary_container']}; border: none; }}
QPushButton#tonal:hover {{ background: {tonal_hover}; }}
QPushButton#text {{ border: none; padding: 5px 10px; color: {C['primary']}; }}
QPushButton#text:hover {{ background: {hover}; }}

QComboBox {{ background: transparent; border: 1px solid {C['outline']}; border-radius: 8px; padding: 5px 10px;
            min-height: 20px; }}
QComboBox:hover {{ border-color: {C['on_surface']}; }}
QComboBox:focus {{ border: 2px solid {C['primary']}; }}
QComboBox:disabled {{ color: {off_fg}; border-color: {off_bg}; }}
QComboBox::drop-down {{ border: none; width: 28px; }}
QComboBox QAbstractItemView {{ background: {C['surface_cont']}; border: none; padding: 6px; outline: none;
                               selection-background-color: {C['secondary_container']}; }}

QPlainTextEdit, QTableWidget {{ background: {C['surface_low']}; border: none; border-radius: 12px; padding: 6px;
                               selection-background-color: {C['primary_container']}; }}
QPlainTextEdit {{ font-family: "Cascadia Mono", Consolas; font-size: 9pt; }}
QPlainTextEdit#editor {{ border: 1px solid {C['outline_variant']}; }}
QTableWidget {{ gridline-color: transparent; }}
QTableWidget::item {{ padding: 3px 6px; border: none; }}
QTableWidget::item:selected {{ background: {C['secondary_container']}; color: {C['on_secondary_container']}; }}
QHeaderView, QHeaderView::section, QTableCornerButton::section {{ background: {C['surface_low']}; border: none; }}
QHeaderView::section {{ color: {C['on_surface_variant']}; padding: 5px 6px; font-weight: 600; }}

QWidget#chip {{ border-radius: 6px; border: 1px solid {C['outline_variant']}; }}
QWidget#chip[ok="yes"] {{ background: {C['good_container']}; border-color: {C['good_container']}; }}
QWidget#chip[ok="no"] {{ background: {C['error_container']}; border-color: {C['error_container']}; }}
QLabel#dot {{ border-radius: 4px; background: {C['outline']}; }}
QLabel#dot[ok="yes"] {{ background: {C['good']}; }}
QLabel#dot[ok="no"] {{ background: {C['error']}; }}
QLabel#chipText {{ font-size: 9pt; font-weight: 600; }}
QLabel#chipText[ok="yes"] {{ color: {C['on_good_container']}; }}
QLabel#chipText[ok="no"] {{ color: {C['on_error_container']}; }}

QProgressBar#thin {{ background: {C['surface_highest']}; border: none; border-radius: 2px; max-height: 4px; }}
QProgressBar#thin::chunk {{ background: {C['primary']}; border-radius: 2px; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {C['outline_variant']}; border-radius: 4px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: {C['outline']}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QToolTip {{ background: {C['surface_highest']}; color: {C['on_surface']}; border: none; padding: 8px; }}
QMessageBox, QInputDialog, QDialog {{ background: {C['surface_high']}; }}
QMenu {{ background: {C['surface_cont']}; padding: 6px 0; }}
QMenu::item {{ padding: 8px 24px; }}
QMenu::item:selected {{ background: {menu_hover}; }}
QLineEdit {{ background: transparent; border: 1px solid {C['outline']}; border-radius: 8px; padding: 5px 8px; }}
QLineEdit:focus {{ border: 2px solid {C['primary']}; }}
QLineEdit[error="yes"] {{ border: 2px solid {C['error']}; }}
"""
