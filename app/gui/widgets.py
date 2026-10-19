"""Custom widgets of the Material 3 look: switch, navigation rail, pages, power button, setting rows."""
from PySide6.QtCore import QEasingCurve, QPointF, QRectF, QSize, Qt, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (QAbstractButton, QButtonGroup, QHBoxLayout, QLabel, QSizePolicy, QStackedWidget,
                               QVBoxLayout, QWidget)

from .style import C, ICON_FONT, glyph


def _alpha(color: str, a: float) -> QColor:
    c = QColor(color)
    c.setAlphaF(a)
    return c


class Switch(QAbstractButton):
    """M3 switch: 52×32 track; the handle grows and slides right when on. Behaves like a QCheckBox."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAttribute(Qt.WA_Hover, True)  # repaint on enter/leave: the hover state layer is drawn by hand
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self._pos = 0.0  # 0 = off, 1 = on (animated)
        self._anim = QVariantAnimation(self, duration=150, easingCurve=QEasingCurve.OutCubic)
        self._anim.valueChanged.connect(self._set_pos)
        self.toggled.connect(self._animate)

    _kbd = False

    def focusInEvent(self, e) -> None:  # noqa: N802
        self._kbd = e.reason() in (Qt.TabFocusReason, Qt.BacktabFocusReason)
        super().focusInEvent(e)

    def sizeHint(self) -> QSize:
        return QSize(60, 40)

    def _set_pos(self, v) -> None:
        self._pos = float(v)
        self.update()

    def _animate(self, on: bool) -> None:
        if not self.isVisible():  # set before shown (settings load): no motion, just the state
            self._set_pos(1.0 if on else 0.0)
            return
        self._anim.stop()
        self._anim.setStartValue(self._pos)
        self._anim.setEndValue(1.0 if on else 0.0)
        self._anim.start()

    def setChecked(self, on: bool) -> None:  # noqa: N802 (Qt API)
        super().setChecked(on)
        if not self.isVisible():
            self._set_pos(1.0 if on else 0.0)

    def showEvent(self, e) -> None:  # noqa: N802
        self._set_pos(1.0 if self.isChecked() else 0.0)
        super().showEvent(e)

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        en = self.isEnabled()
        t = self._pos
        track = QRectF(4, 4, 52, 32)
        on_track, off_track = QColor(C["primary"]), QColor(C["surface_highest"])
        mix = QColor.fromRgbF(*(off_track.getRgbF()[i] * (1 - t) + on_track.getRgbF()[i] * t for i in range(3)))
        if not en:
            mix = _alpha(C["on_surface"], .12)
        p.setPen(QPen(QColor(C["outline"]) if en else _alpha(C["on_surface"], .12), 2) if t < .5 else Qt.NoPen)
        p.setBrush(mix)
        p.drawRoundedRect(track.adjusted(1, 1, -1, -1) if t < .5 else track, 16, 16)
        d = 16 + 8 * t + (4 if self.isDown() else 0)
        cx, cy = 4 + 16 + 20 * t, 20.0
        kbd = self.hasFocus() and self._kbd
        if (self.underMouse() or kbd) and en:  # state layer; focus shows only when it came from the keyboard
            p.setPen(Qt.NoPen)
            p.setBrush(_alpha(C["primary"] if t > .5 else C["on_surface"], .12 if kbd else .08))
            p.drawEllipse(QPointF(cx, cy), 20, 20)
        p.setPen(Qt.NoPen)
        handle = QColor(C["on_primary"]) if t > .5 else QColor(C["outline"])
        if not en:
            handle = _alpha(C["on_surface"], .38)
        p.setBrush(handle)
        p.drawEllipse(QPointF(cx, cy), d / 2, d / 2)
        if t > .5:  # check mark inside the handle: the state is readable without color
            p.setPen(QPen(QColor(C["on_primary_container"] if en else C["surface"]), 2, Qt.SolidLine,
                          Qt.RoundCap, Qt.RoundJoin))
            path = QPainterPath(QPointF(cx - 5, cy))
            path.lineTo(cx - 1.5, cy + 3.5)
            path.lineTo(cx + 5, cy - 3.5)
            p.drawPath(path)
        p.end()

    def hitButton(self, pos) -> bool:  # noqa: N802
        return self.rect().contains(pos)


class SettingRow(QWidget):
    """One line: title on the left, a switch on the right; the description shows on hover (row and switch).
    A click anywhere on the row flips the switch."""

    def __init__(self, title: str, text: str, switch: Switch, parent=None):
        super().__init__(parent)
        self.setObjectName("row")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(text)
        self.switch = switch
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 0, 4, 0)
        lay.setSpacing(12)
        self.title = QLabel(title, objectName="rowTitle")
        lay.addWidget(self.title, 1)
        lay.addWidget(switch, 0, Qt.AlignVCenter)

    def mouseReleaseEvent(self, e) -> None:  # noqa: N802
        if e.button() == Qt.LeftButton and self.switch.isEnabled() and self.rect().contains(e.position().toPoint()):
            self.switch.toggle()
        super().mouseReleaseEvent(e)

    def changeEvent(self, e) -> None:  # noqa: N802
        super().changeEvent(e)
        self.title.setEnabled(self.switch.isEnabled())


class InputRow(QWidget):
    """One line: title on the left, a short text field on the right; the description shows on hover."""

    def __init__(self, title: str, text: str, edit, parent=None):
        super().__init__(parent)
        self.setObjectName("row")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setToolTip(text)
        self.edit = edit
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 4, 8, 4)
        lay.setSpacing(12)
        lay.addWidget(QLabel(title, objectName="rowTitle"), 1)
        edit.setToolTip(text)
        edit.setFixedWidth(190)
        lay.addWidget(edit, 0, Qt.AlignVCenter)


class RailButton(QAbstractButton):
    """Navigation rail destination: icon in a pill-shaped active indicator, label below."""

    def __init__(self, icon: str, text: str, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setText(text)
        self._icon = icon
        self.setCursor(Qt.PointingHandCursor)
        self.setAttribute(Qt.WA_Hover, True)
        self.setFocusPolicy(Qt.NoFocus)  # Ctrl+1..6 switch pages; a focus pill on start looked like a 2nd selection
        self.setFixedSize(68, 54)
        self.setToolTip(text)

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        on = self.isChecked()
        pill = QRectF((self.width() - 48) / 2, 4, 48, 28)
        if on or self.underMouse():
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(C["secondary_container"]) if on else _alpha(C["on_surface"], .08))
            p.drawRoundedRect(pill, 14, 14)
        f = QFont(ICON_FONT)
        f.setPixelSize(16)
        p.setFont(f)
        p.setPen(QColor(C["on_secondary_container"] if on else C["on_surface_variant"]))
        p.drawText(pill, Qt.AlignCenter, glyph(self._icon))
        f = QFont(self.font())
        f.setPixelSize(11)
        f.setWeight(QFont.DemiBold if on else QFont.Medium)
        p.setFont(f)
        p.setPen(QColor(C["on_surface"] if on else C["on_surface_variant"]))
        p.drawText(QRectF(0, 34, self.width(), 16), Qt.AlignHCenter | Qt.AlignTop, self.text())
        p.end()


class Rail(QWidget):
    """Vertical navigation rail; emits the index of the chosen destination."""
    changed = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("rail")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setFixedWidth(76)
        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(4, 10, 4, 10)
        self._lay.setSpacing(4)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._group.idClicked.connect(self.changed.emit)
        self._lay.addStretch(1)
        self.buttons: list[RailButton] = []

    def add(self, icon: str, text: str) -> RailButton:
        b = RailButton(icon, text)
        self._group.addButton(b, len(self.buttons))
        self._lay.insertWidget(self._lay.count() - 1, b, 0, Qt.AlignHCenter)
        self.buttons.append(b)
        if len(self.buttons) == 1:
            b.setChecked(True)
        return b

    def select(self, i: int) -> None:
        if 0 <= i < len(self.buttons):
            self.buttons[i].setChecked(True)


class Pages(QStackedWidget):
    """A stack of pages driven by a Rail, with the QTabWidget calls the window and tests use."""

    def __init__(self, rail: Rail, parent=None):
        super().__init__(parent)
        self.rail = rail
        rail.changed.connect(self.setCurrentIndex)
        self.currentChanged.connect(rail.select)

    def addTab(self, w: QWidget, text: str, icon: str = "home") -> int:  # noqa: N802
        self.rail.add(icon, text)
        return self.addWidget(w)

    def setTabVisible(self, i: int, on: bool) -> None:  # noqa: N802
        self.rail.buttons[i].setVisible(on)
        if not on and self.currentIndex() == i:
            self.setCurrentIndex(0)


S = 88  # power button size, px


class PowerButton(QAbstractButton):
    """The one bold element: a big round toggle whose ring tells the state (off / on / working)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.state = "off"
        self.setCursor(Qt.PointingHandCursor)
        self.setAttribute(Qt.WA_Hover, True)
        self.setFixedSize(S, S)
        self._spin = 0.0
        self._anim = QVariantAnimation(self, startValue=0.0, endValue=360.0, duration=1400, loopCount=-1)
        self._anim.valueChanged.connect(self._rot)

    _kbd = False

    def focusInEvent(self, e) -> None:  # noqa: N802
        self._kbd = e.reason() in (Qt.TabFocusReason, Qt.BacktabFocusReason)
        super().focusInEvent(e)

    def _rot(self, v) -> None:
        self._spin = float(v)
        self.update()

    def set_state(self, state: str) -> None:
        if state == self.state:
            return
        self.state = state
        if state == "busy":
            self._anim.start()
        else:
            self._anim.stop()
        self.update()

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        ring = {"on": C["good"], "busy": C["warn"]}.get(self.state, C["outline_variant"])
        fill = {"on": C["good_container"], "busy": C["warn_container"]}.get(self.state, C["surface_high"])
        fg = {"on": C["good"], "busy": C["warn"]}.get(self.state, C["on_surface_variant"])
        r = QRectF(4, 4, S - 8, S - 8)
        if self.underMouse() and self.isEnabled():
            p.setPen(Qt.NoPen)
            p.setBrush(_alpha(fg, .10))
            p.drawEllipse(QRectF(0, 0, S, S))
        # busy: a dim ring, so the bright moving arc on it is visible (same color before: no motion was seen)
        p.setPen(QPen(_alpha(ring, .3) if self.state == "busy" else QColor(ring), 3))
        p.setBrush(QColor(fill))
        p.drawEllipse(r)
        if self.state == "busy":  # a moving arc: work in progress
            p.setPen(QPen(QColor(C["warn"]), 3, Qt.SolidLine, Qt.RoundCap))
            p.setBrush(Qt.NoBrush)
            p.drawArc(r, int(-self._spin * 16), 90 * 16)
        if self.hasFocus() and self._kbd:  # ring only for keyboard focus, not on start / after a click
            p.setPen(QPen(QColor(C["primary"]), 2))
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(QRectF(1, 1, S - 2, S - 2))
        pen = QPen(QColor(fg), 4.5, Qt.SolidLine, Qt.RoundCap)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        cx, cy, rr = S / 2, S / 2 + 2, S * .17
        p.drawArc(QRectF(cx - rr, cy - rr, 2 * rr, 2 * rr), 125 * 16, 290 * 16)
        p.drawLine(QPointF(cx, cy - rr - 5), QPointF(cx, cy - 4))
        p.end()


class StatusChip(QWidget):
    """Service status: a colored dot and the name; the text says the state, the dot repeats it."""

    def __init__(self, name: str, ok: bool | None, note: str = "", parent=None):
        super().__init__(parent)
        self.setObjectName("chip")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setProperty("ok", "none" if ok is None else ("yes" if ok else "no"))
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 4, 12, 4)
        lay.setSpacing(8)
        self.dot = QLabel(objectName="dot")
        self.dot.setFixedSize(8, 8)
        self.dot.setProperty("ok", self.property("ok"))
        note = note or ("" if ok is None or ok else "нет")  # short: the chips share one row
        lab = QLabel(name + (f": {note}" if note else ""), objectName="chipText")
        lab.setProperty("ok", self.property("ok"))
        lay.addWidget(self.dot)
        lay.addWidget(lab)
        self.setToolTip(f"{name}: " + ("не проверено" if ok is None else
                                       ("работает" if ok else ("медленно" if note == "медленно" else "не открывается"))))
